from __future__ import annotations

import json
import os
import re
import shlex
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from greedy_token.executors import ProductInvocation
from greedy_token.hook_host import BYPASS_PREFIXES, extract_hit_files, strip_ask_prefix

#
# Policy — single source: advisory by default.  The threshold below sits above
# max confidence (1.0), so every routed prompt is logged, never blocked.
# Blocking is explicit opt-in: GREEDY_HOOK_MODE=gate (block + point at
# `greedy-token invoke <op>`, nothing executed) or =intercept (execute the
# cheap op, answer in the toast).  Mode parsing lives in advisory.py; the raw
# GREEDY_HOOK_MIN_CONFIDENCE=<value ≤1.0> env still enables legacy intercept
# when no mode is set.  The .sh launcher stays policy-free so direct
# `python …route.py` runs behave identically to the wired hook.
# (Agent-overkill blocking is a separate opt-in: GREEDY_OVERKILL_GATE=1 —
# see advisory.py.)
ADVISORY_MIN_CONFIDENCE = 1.01
MIN_PROMPT_LEN = 12
ASK_GATE_DIR = Path.home() / ".greedy-token" / "ask-gate"


@dataclass(frozen=True)
class HookResponse:
    kind: Literal["pass", "gate", "intercept"] = "pass"
    payload: dict[str, Any] = field(default_factory=dict)


def set_ask_gate(session_id: str | None, active: bool) -> None:
    ASK_GATE_DIR.mkdir(parents=True, exist_ok=True)
    path = ASK_GATE_DIR / (f"{session_id}.active" if session_id else "active")
    if not active:
        path.unlink(missing_ok=True)
        return
    path.write_text(
        json.dumps({"active": True, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def min_confidence_threshold() -> float:
    """Fallback threshold when the package is unavailable — advisory default."""
    raw = os.environ.get("GREEDY_HOOK_MIN_CONFIDENCE", "").strip()
    if not raw:
        return ADVISORY_MIN_CONFIDENCE
    try:
        return float(raw)
    except ValueError:
        return ADVISORY_MIN_CONFIDENCE


def _invocable_op(root: Path, route_id: str) -> Any:
    """Capability row for the matched route — invocable only, else None.

    Gate mode may block only for ops the capabilities surface calls
    invocable (ready + read-only + tool/python/ollama tier); everything
    else fails open to Agent.
    """
    try:
        from greedy_token.capabilities import capability_by_id

        op = capability_by_id(root, route_id)
    except Exception:
        return None
    if (
        getattr(op, "invocable", False) is not True
        or getattr(op, "read_only", False) is not True
        or getattr(op, "readiness", "") != "ready"
    ):
        return None
    return op


def _args_from_prompt_spec(op_id: str, root: Path | None) -> list[dict]:
    """Route-declared prompt→args specs — the opt-in that lets a
    params:[args] op derive argv from the prompt instead of skipping intent."""
    try:
        from greedy_token.paths import find_workspace_root, load_routes_config

        routes = load_routes_config(root or find_workspace_root()).get("routes", [])
    except (Exception, SystemExit):
        return []
    route = next((r for r in routes if r.get("id") == op_id), None)
    spec = route.get("args_from_prompt") if isinstance(route, dict) else None
    return spec if isinstance(spec, list) else []


def derive_prompt_args(
    prompt: str, spec: list[dict], *, root: Path | None = None, path_hint: str | None = None,
) -> str:
    """Render the first matching args_from_prompt entry as an args string.

    ``{0}``..``{n}`` in ``args`` map to regex capture groups; a spec whose
    regex misses or whose template does not line up yields "" — fail-open,
    the fixed argv still stands.
    """
    from greedy_token.router import (
        _JSON_KEYS_HINT,
        parse_json_keys_intent,
        parse_recent_commits_intent,
    )
    from greedy_token.subprocess_safe import UnsafeCommandError, _validate_script_args

    text = prompt.strip()
    if _JSON_KEYS_HINT.search(text):
        if root is None:
            from greedy_token.paths import find_workspace_root

            try:
                root = find_workspace_root()
            except SystemExit as exc:
                raise UnsafeCommandError("prompt derivation workspace is unresolved") from exc
        slots = parse_json_keys_intent(prompt, root, path_hint=path_hint)
    else:
        slots = parse_recent_commits_intent(prompt)
    allowed = None
    if slots is not None and slots.intent == "json_keys":
        keys = ",".join(slots.keys)
        allowed = {
            ("--path", slots.path, "--keys", keys), (slots.path, "--keys", keys),
        }
    elif slots is not None:
        allowed = {("--count", str(slots.count)), ("--count", str(slots.count), "--compact")} if slots.count is not None else {("--compact",)}
    for entry in spec:
        if not isinstance(entry, dict):
            continue
        regex = str(entry.get("regex") or "")
        template = str(entry.get("args") or "")
        if not regex or not template:
            continue
        try:
            match = re.search(regex, text, flags=re.IGNORECASE)
        except re.error:
            continue
        if match is None:
            continue
        try:
            derived = template.format(*(g or "" for g in match.groups()))
            argv = tuple(shlex.split(derived))
        except (IndexError, KeyError, ValueError, AttributeError):
            continue
        if not argv:
            return ""
        if root is None:
            from greedy_token.paths import find_workspace_root

            try:
                root = find_workspace_root()
            except SystemExit as exc:
                raise UnsafeCommandError("prompt derivation workspace is unresolved") from exc
        _validate_script_args(argv, root)
        if allowed is None:
            raise UnsafeCommandError("unsupported prompt derivation intent")
        if argv not in allowed:
            raise UnsafeCommandError("prompt derivation contradicts admitted slots")
        return derived
    return ""


def has_invocation_intent(prompt: str, op: Any, *, root: Path | None = None) -> bool:
    from greedy_token.subprocess_safe import UnsafeCommandError

    try:
        return _has_invocation_intent(prompt, op, root=root)
    except UnsafeCommandError:
        return False


def _has_invocation_intent(prompt: str, op: Any, *, root: Path | None = None) -> bool:
    from greedy_token.router import (
        _JSON_KEYS_HINT,
        _has_repository_scope,
        _json_route_intent,
        has_edit_verbs,
        is_read_only_tool_intent,
        parse_recent_commits_intent,
    )

    args_spec: list[dict] = []
    if op.params and op.params != ("query",):
        args_spec = _args_from_prompt_spec(getattr(op, "id", ""), root)
        if not args_spec:
            return False
    if op.tier == "tool":
        return is_read_only_tool_intent(prompt)
    if _has_repository_scope(prompt):
        return False
    if _JSON_KEYS_HINT.search(prompt):
        if root is None:
            from greedy_token.paths import find_workspace_root

            try:
                root = find_workspace_root()
            except SystemExit:
                return False
        route = {
            "target": op.tier, "read_only": op.read_only, "patterns": op.patterns,
            "command": getattr(op, "command", ""), "argv": getattr(op, "argv", ()),
            "params": op.params, "args_from_prompt": args_spec,
        }
        return _json_route_intent(prompt, route, root) is not None
    if args_spec:
        slots = parse_recent_commits_intent(prompt)
        if slots is not None and slots.count is not None:
            return bool(derive_prompt_args(prompt, args_spec, root=root))
        derive_prompt_args(prompt, args_spec, root=root)
        if slots is None and re.search(r"\b(?:коммит\w*|commits?)\b|(?:^|\s)--?\w|[=$<>|]", prompt, re.IGNORECASE):
            return False
    text = re.sub(r"^(?:please|пожалуйста)[,\s]+", "", prompt.strip(), flags=re.IGNORECASE)
    if re.search(
        r"""["'`«»“”‘’]|[;,\r\n]|\b(?:не|нет|нельзя|никогда|not|never|no|without|instead|"""
        r"and|or|then|after|и|или|затем|потом|после|вместо|if|если|suppose|hypothetical|"
        r"explain|describe|объясни|расскажи|напиши)\b",
        text, flags=re.IGNORECASE,
    ) or has_edit_verbs(text):
        return False
    text = " ".join(text.lower().split()).rstrip(" .!?")
    aliases = {" ".join(p.lower().split()).rstrip(" .!?") for p in op.patterns if p.strip()}
    if all(part in aliases for part in re.split(r"\s*[—–]\s*", text)):
        return True
    words = text.split()
    alias_words = [alias.split() for alias in aliases]
    if any(
        words[:len(first)] == first and words[len(first) - 1:] == second
        for first in alias_words for second in alias_words
        if first[-1] == second[0]
    ):
        return True
    request = re.match(
        r"^(?:show|list|check|verify|run|audit|evaluate|why|"
        r"покажи|выведи|дай|список|проверь|запусти|почему)\b|"
        r"^what\s+changed\b|^что\s+(?:изменилось|нагружает|жрёт)\b|^какие\s+процессы\b",
        text,
    )
    if request is None:
        return False
    if args_spec and derive_prompt_args(prompt, args_spec, root=root):
        return True
    suffixes = {
        "", "now", "please", "сейчас", "пожалуйста", "проекта", "workspace",
        "в workspace", "для workspace", "в этом репо", "в репозитории",
        "в этом репозитории", "этого репозитория", "for this workspace",
        "in this workspace", "in this repository", "in the repository",
        "for this repository",
    }
    return any(
        candidate.startswith(alias) and candidate[len(alias):].strip() in suffixes
        for candidate in (text, text[request.end():].strip())
        for alias in aliases
    )


def _pass_to_agent(
    prompt: str, data: dict, decision: Any, adv: Any, action: str,
) -> HookResponse:
    if adv is not None:
        adv.emit_advisory(
            adv.build_event(
                kind=adv.KIND_PASS, action=action, prompt=prompt, decision=decision, data=data,
            )
        )
    return HookResponse()


def _try_advisory():
    try:
        from greedy_token import advisory

        return advisory
    except ImportError:
        return None


def _log_bypass(prompt: str, data: dict[str, Any]) -> None:
    adv = _try_advisory()
    if adv is None:
        return

    class _BypassDecision:
        target = "cursor"
        route_id = "bypass"
        confidence = 1.0
        est_tokens = 0

    adv.emit_advisory(
        adv.build_event(
            kind=adv.KIND_BYPASS,
            action="pass",
            prompt=prompt,
            decision=_BypassDecision(),
            data=data,
        )
    )


def _handle_cursor_route(
    prompt: str, data: dict[str, Any], decision: Any, adv: Any,
) -> HookResponse | None:
    """Return a blocking response only when the overkill gate is enabled."""
    attachments = adv.parse_attachments(data)
    if not adv.is_overkill(
        prompt,
        route_id=decision.route_id,
        target=decision.target,
        attachment_count=len(attachments),
    ):
        adv.emit_advisory(
            adv.build_event(
                kind=adv.KIND_PASS,
                action="pass",
                prompt=prompt,
                decision=decision,
                data=data,
            )
        )
        return None

    recs = adv.overkill_recommendations(
        prompt=prompt,
        attachment_count=len(attachments),
        est_tokens=decision.est_tokens,
        route_id=decision.route_id,
    )
    blocked = adv.overkill_gate_enabled()
    adv.emit_advisory(
        adv.build_event(
            kind=adv.KIND_OVERKILL,
            action="blocked" if blocked else "warn",
            prompt=prompt,
            decision=decision,
            data=data,
            blocked=blocked,
            recommendations=recs,
        )
    )
    if blocked:
        return HookResponse("gate", {
            "continue": False,
            "user_message": adv.format_overkill_user_message(
                prompt,
                attachment_count=len(attachments),
                est_tokens=decision.est_tokens,
                route_id=decision.route_id,
            ),
        })
    return None


def evaluate(
    prompt: str, data: dict[str, Any], *, soft_gate: bool = False,
    invocation: ProductInvocation | None = None,
) -> HookResponse:
    if invocation is None:
        return _evaluate(prompt, data, soft_gate=soft_gate)
    from greedy_token.paths import find_workspace_root

    data = deepcopy(data)
    root = find_workspace_root()
    adv = _try_advisory()
    mode = adv.hook_mode() if adv is not None else ""
    threshold = adv.hook_min_confidence() if adv is not None else min_confidence_threshold()
    attachments = tuple(adv.parse_attachments(data)) if adv is not None else ()
    overkill_gate = adv.overkill_gate_enabled() if adv is not None else False
    return invocation.run(
        lambda: _evaluate(prompt, data, soft_gate=soft_gate, invocation=invocation, root=root),
        root=root, op="hook_policy",
        params=(
            prompt, data.get("session_id") or data.get("conversation_id"), attachments,
            soft_gate, mode, threshold, overkill_gate,
        ),
        request_id=data.get("request_id"), prompt_id=data.get("prompt_id"),
        input_version=data.get("input_version"),
    )


def _evaluate(
    prompt: str, data: dict[str, Any], *, soft_gate: bool = False,
    invocation: ProductInvocation | None = None, root: Path | None = None,
) -> HookResponse:
    session_id = data.get("session_id") or data.get("conversation_id")
    if not prompt or len(prompt) < MIN_PROMPT_LEN:
        set_ask_gate(session_id, False)
        return HookResponse()

    lower = prompt.lower()
    if lower.startswith(BYPASS_PREFIXES):
        set_ask_gate(session_id, False)
        _log_bypass(prompt, data)
        return HookResponse()

    prompt, ask_only = strip_ask_prefix(prompt)
    set_ask_gate(session_id, ask_only)

    try:
        from greedy_token.capabilities_invoke import invoke_capability
        from greedy_token.paths import find_workspace_root
        from greedy_token.result_gate import GATE_ACCEPTED, evaluate_result_gate
        from greedy_token.router import route_task
    except ImportError:
        return HookResponse()

    root = root or find_workspace_root()
    decision = route_task(prompt, root)
    adv = _try_advisory()

    if decision.target == "cursor":
        if adv is not None:
            response = _handle_cursor_route(prompt, data, decision, adv)
            if response is not None:
                return response
        return HookResponse()

    if adv is not None:
        mode = adv.hook_mode()
        threshold = adv.hook_min_confidence()
    else:
        mode = ""
        threshold = min_confidence_threshold()
    is_advisory = mode == "advisory"
    if is_advisory or decision.confidence < threshold:
        if adv is not None:
            adv.emit_advisory(
                adv.build_event(
                    kind=adv.KIND_PASS,
                    action="advisory" if is_advisory else "low_confidence",
                    prompt=prompt,
                    decision=decision,
                    data=data,
                )
            )
        return HookResponse()

    op = _invocable_op(root, decision.route_id)
    if op is None:
        action = "gate_skip" if mode == "gate" else "intercept_skip"
        return _pass_to_agent(prompt, data, decision, adv, action)
    if not has_invocation_intent(prompt, op, root=root):
        return _pass_to_agent(prompt, data, decision, adv, "intent_skip")

    if adv is not None and mode == adv.HOOK_MODE_GATE:
        # Gate: block with an invoke pointer, never auto-execute.  Only an
        # invocable capability may stop a prompt; anything else fails open.
        adv.emit_advisory(
            adv.build_event(
                kind=adv.KIND_GATE,
                action="soft_gate" if soft_gate else "blocked",
                prompt=prompt,
                decision=decision,
                data=data,
                blocked=not soft_gate,
            )
        )
        if soft_gate:
            return HookResponse("gate", {"op_id": decision.route_id})
        return HookResponse("gate", {
            "continue": False,
            "user_message": adv.format_gate_user_message(prompt, op_id=decision.route_id),
        })

    try:
        if op.params == ("query",):
            # Raw prompt as task: the rg argv builder extracts the pattern AND
            # prompt path scopes itself — a verbatim --query would lose the path.
            params = {"task": prompt}
        elif op.params == ("args",):
            derived = derive_prompt_args(prompt, _args_from_prompt_spec(op.id, root), root=root)
            params = {"args": derived} if derived else {}
        else:
            params = {}
        if invocation is None:
            result = invoke_capability(root, op.id, **params)
        else:
            result = invocation.dispatch(
                lambda: invoke_capability(root, op.id, **params), cause="executor",
            )
    except Exception:
        return _pass_to_agent(prompt, data, decision, adv, "execution_error")
    # One policy rules on the run result: a refusal (not_started), an invalid
    # contract claim, an empty/unusable output, or a crashed run never
    # intercepts — those pass through to Agent.  An unverified contract-tier
    # result (script ran clean but declared no canon contract) still may
    # answer when the output is useful — it just claims no savings.
    gate = evaluate_result_gate(
        started=result.executed,
        result_status=result.result_status,
        tier=result.tier,
        ok=result.exit_code == 0,
        output_useful=not cheap_output_empty(
            result.output,
            target=result.tier,
            exit_code=result.exit_code,
        ),
    )
    if result.gate_action != GATE_ACCEPTED or not gate.may_answer:
        reason = result.gate_reason if result.gate_action != GATE_ACCEPTED else gate.reason
        return _pass_to_agent(prompt, data, decision, adv, reason)

    target = result.tier.upper()
    if adv is not None:
        adv.emit_advisory(
            adv.build_event(
                kind=adv.KIND_INTERCEPT,
                action="blocked",
                prompt=prompt,
                decision=decision,
                data=data,
                blocked=True,
            )
        )
    pretty = adv.render_result_output(result.output) if adv is not None else None
    return HookResponse("intercept", {
        "target": target, "prompt": prompt, "body": result.output, "pretty": pretty, "root": root,
    })


def cheap_output_empty(output: str, *, target: str, exit_code: int = 0) -> bool:
    """True when cheap-tier result is useless — Agent should handle the prompt."""
    text = (output or "").strip()
    if not text:
        return True
    if text.startswith("No RAG hits"):
        return True
    if target == "tool":
        if exit_code not in (0, 1):
            return True
        if exit_code == 1 and not extract_hit_files(text):
            return True
    return False
