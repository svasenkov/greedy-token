from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

# Cursor "Submission blocked by hook" dialog does not scroll — keep toast short.
# Full answer is spilled to a file; the toast only shows a short preview + link.
MAX_PROMPT_PREVIEW = 120
MAX_BODY_PREVIEW = 240
BYPASS_PREFIXES = ("cursor:", "agent:", "gt-skip:", "nogreedy:")
ASK_PREFIXES = ("ask:", "?")
INTERCEPT_SPILL = Path.home() / ".greedy-token" / "last-intercept.md"
BYPASS_HINT = "Чтобы запустить без greedy — начните промпт с `nogreedy:`"

_HIT_PATH_RE = re.compile(
    r"^((?:[A-Za-z]:)?[^:\n]+\.[A-Za-z0-9_+-]+|\.?/?[\w./+-]+\.[A-Za-z0-9_+-]+):(\d+):"
)

# Devin inlines injected context (e.g. the whole <project_context> session
# summary blob, ~130KB) into the UserPromptSubmit `prompt` field on the first
# message of a session — the user's real text sits outside those blocks.
# Routing must see only the real text or it matches ops named in the blob.
_INJECTED_TAGS = (
    "project_context",
    "system_info",
    "additional_metadata",
    "rules",
    "available_rules",
    "available_skills",
)
_INJECTED_OPEN_RE = re.compile(
    r"<(?P<tag>" + "|".join(_INJECTED_TAGS) + r")(?:\s[^>]*)?>"
)


def strip_injected_context(prompt: str) -> str:
    """Drop leading injected context blocks; keep the user's trailing text.

    The blob may itself contain literal "</project_context>" strings (e.g. a
    session summary quoting a fix description), so the real close is the
    LAST one — rfind, not a non-greedy regex.  Loop to strip successive
    leading blocks; fail open on unclosed/empty results.
    """
    text = prompt
    while True:
        m = _INJECTED_OPEN_RE.match(text.lstrip())
        if not m:
            break
        close = f"</{m.group('tag')}>"
        end = text.rfind(close)
        if end == -1:
            break
        text = text[end + len(close) :].lstrip()
    return text.strip() or prompt


def strip_ask_prefix(prompt: str) -> tuple[str, bool]:
    lower = prompt.lower()
    for prefix in ASK_PREFIXES:
        if lower.startswith(prefix):
            rest = prompt[len(prefix) :].lstrip()
            return rest or prompt, True
    return prompt, False


def _parse_cursor_prompt(data: dict[str, Any]) -> str:
    prompt = data.get("prompt")
    return prompt.strip() if isinstance(prompt, str) else ""


def _parse_devin_prompt(data: dict[str, Any]) -> str:
    return strip_injected_context(_parse_cursor_prompt(data))


def _parse_unknown_prompt(data: Any) -> str:
    return ""


def _serialize_cursor(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _serialize_devin(payload: dict[str, Any]) -> str:
    if payload.get("continue", True):
        return "{}"
    out = {
        "decision": "block",
        "reason": payload.get("user_message") or "greedy-token",
    }
    return json.dumps(out, ensure_ascii=False)


def _serialize_unknown(payload: dict[str, Any]) -> str:
    return "{}"


def _serialize_devin_context(context: str) -> str:
    """Devin soft-gate: inject context, the prompt still reaches the agent."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    return json.dumps(out, ensure_ascii=False)


def _devin_soft_gate_enabled() -> bool:
    raw = os.environ.get("GREEDY_DEVIN_SOFT_GATE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def format_devin_gate_context(op_id: str) -> str:
    """Agent-side instruction for the Devin soft-gate (injected context)."""
    return (
        "greedy-token soft-gate: this prompt matches the invocable "
        f"operation `{op_id}` (trusted, read-only). Call the "
        f'`greedy_token_invoke` MCP tool with op_id="{op_id}" and answer '
        "from its output instead of doing the work manually. If the op "
        "fails or its output does not answer the prompt, proceed normally."
    )


@dataclass(frozen=True)
class HostProfile:
    name: str
    can_render_full: bool
    link_style: Literal["ref_chip", "file_uri", "plain_path"]
    _parser: Callable[[dict[str, Any]], str]
    _encoder: Callable[[dict[str, Any]], str]
    supported: bool = True
    soft_gate_enabled: bool = False
    _context_encoder: Callable[[str], str] | None = None

    @property
    def supports_context_injection(self) -> bool:
        return self._context_encoder is not None

    def parse_prompt(self, data: dict[str, Any]) -> str:
        return self._parser(data)

    def serialize_pass(self, payload: dict[str, Any] | None = None) -> str:
        return self._encoder(payload or {"continue": True})

    def _record_observation(
        self, action: str, serialized: bool, payload: dict[str, Any]
    ) -> None:
        """Ledger fact: the product-side boundary serialization act.

        Records only what this process observed — the requested action and
        whether the host-facing response was produced (``serialized``).
        Whether the host then skipped the model turn is a host-side fact
        this boundary cannot attest; ``build_hook_observation_event`` keeps
        it unknown.  Telemetry must never break the hook boundary, so
        failures are swallowed (append_event already degrades on OSError).
        """
        try:
            from greedy_token.usage import (
                append_event,
                build_hook_observation_event,
            )

            append_event(
                build_hook_observation_event(
                    host=self.name,
                    action=action,
                    serialized=serialized,
                    task=str(payload.get("task") or payload.get("prompt") or ""),
                    root=payload.get("root") or None,
                    route_id=payload.get("op_id") or payload.get("route_id"),
                    operation_id=payload.get("operation_id"),
                )
            )
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _emit_observed(
        self, action: str, payload: dict[str, Any], encode: Callable[[], str]
    ) -> str:
        """Serialize the response, then record the fact of that act.

        A failed serialization means no response reached the host — the
        observation records ``serialized=False`` and the error propagates.
        """
        try:
            out = encode()
        except Exception:
            self._record_observation(action, False, payload)
            raise
        self._record_observation(action, True, payload)
        return out

    def serialize_gate(self, payload: dict[str, Any]) -> str:
        if self.soft_gate_enabled and self._context_encoder is not None and (
            op_id := payload.get("op_id")
        ):
            # Context is injected but the prompt still reaches the agent —
            # the turn is shared, not replaced.
            return self._emit_observed(
                "soft_gate",
                payload,
                lambda: self._context_encoder(format_devin_gate_context(op_id)),
            )
        resp = {
            "continue": False,
            "user_message": payload.get("user_message") or "greedy-token",
        }
        if not self.supported:
            return self._encoder(resp)
        # Emitting a blocking response is a skip request, not a proven host
        # skip — the observation records the serialized act only.
        return self._emit_observed("gate", payload, lambda: self._encoder(resp))

    def serialize_intercept(self, payload: dict[str, Any]) -> str:
        if not self.supported:
            return self.serialize_pass()
        message = format_user_message(
            payload["target"], payload["prompt"], payload["body"], payload.get("pretty"),
            full=self.can_render_full, link_style=self.link_style, root=payload.get("root"),
        )
        return self._emit_observed(
            "intercept",
            payload,
            lambda: self._encoder({"continue": False, "user_message": message}),
        )

    def serialize(self, kind: str, payload: dict[str, Any] | None = None) -> str:
        serializer = {
            "pass": self.serialize_pass,
            "gate": self.serialize_gate,
            "intercept": self.serialize_intercept,
        }.get(kind)
        return serializer(payload or {}) if serializer is not None else self.serialize_pass()


_CURSOR = HostProfile("cursor", False, "file_uri", _parse_cursor_prompt, _serialize_cursor)
_DEVIN = HostProfile(
    "devin", True, "ref_chip", _parse_devin_prompt, _serialize_devin,
    _context_encoder=_serialize_devin_context,
)
_CODEX = HostProfile(
    "codex", False, "plain_path", _parse_cursor_prompt, _serialize_devin,
)
_UNKNOWN = HostProfile(
    "unknown", False, "plain_path", _parse_unknown_prompt, _serialize_unknown, supported=False,
)

# Output contract: Cursor expects {"continue", "user_message"}; Devin and
# Codex (UserPromptSubmit) expect {"decision": "block", "reason"}.  Devin
# stdin carries "hook_event_name" — Cursor never sends it, so its presence
# selects the block translation; a bare event name stays the devin profile.
# Codex sends the same event name plus the always-serialized
# UserPromptSubmitCommandInput fields — any of the codex-only keys below
# (turn_id is a codex extension; transcript_path/permission_mode are
# Claude-schema fields devin does not send) selects the codex profile, so
# ledger rows stop being mislabeled devin.  Policy stays editor-agnostic.
_CODEX_FIELDS = frozenset(("turn_id", "transcript_path", "permission_mode"))


def detect(data: Any) -> HostProfile:
    if not isinstance(data, dict):
        return _UNKNOWN
    if "hook_event_name" not in data:
        return _CURSOR
    if data["hook_event_name"] != "UserPromptSubmit":
        return _UNKNOWN
    if not _CODEX_FIELDS.isdisjoint(data):
        return _CODEX
    return replace(_DEVIN, soft_gate_enabled=_devin_soft_gate_enabled())


def extract_hit_files(body: str, *, limit: int = 5) -> list[str]:
    seen: list[str] = []
    for line in body.splitlines():
        m = _HIT_PATH_RE.match(line.strip())
        if not m:
            continue
        path = m.group(1)
        if path.startswith("Search:") or path.startswith("---"):
            continue
        if path not in seen:
            seen.append(path)
        if len(seen) >= limit:
            break
    return seen


def _preview(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _preview_lines(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\n…"


def spill_intercept(
    target: str, prompt: str, body: str, pretty: str, root: Path | None = None
) -> Path:
    """Full intercept payload — file links must live inside the workspace."""
    spill = (
        Path(root) / ".greedy-token" / "last-intercept.md"
        if root is not None
        else INTERCEPT_SPILL
    )
    spill.parent.mkdir(parents=True, exist_ok=True)
    tail = ""
    if pretty != body:
        tail = f"\n\n## Raw output\n\n```json\n{body.strip()}\n```\n"
    spill.write_text(
        f"# greedy-token → {target}\n\n"
        f"## Задача\n\n{prompt.strip()}\n\n"
        f"## Ответ\n\n{pretty.strip()}\n{tail}",
        encoding="utf-8",
    )
    return spill


def _spill_link(spill: Path, style: str) -> str:
    if style == "ref_chip":
        return f'<ref_file file="{spill}" />'
    if style == "file_uri":
        return f"[{spill.name}]({spill.as_uri()})"
    return str(spill)


def format_user_message(
    target: str,
    prompt: str,
    body: str,
    pretty: str | None = None,
    *,
    full: bool = False,
    link_style: str | None = None,
    root: Path | None = None,
) -> str:
    text = body.strip()
    disp = (pretty or text).strip()
    spill = spill_intercept(target, prompt, text, disp, root)
    link = _spill_link(spill, link_style or ("ref_chip" if full else "file_uri"))
    prompt_preview = _preview(prompt, MAX_PROMPT_PREVIEW)

    lines = [
        # Honest claim: the answer was produced locally and a blocking
        # response was emitted — whether the host skipped the model turn is
        # a host-side fact this process cannot attest.
        f"greedy-token → {target} · локальный ответ",
        "",
        f"Q: {prompt_preview}",
    ]
    files = extract_hit_files(text)
    if full:
        # Scrollable hosts: whole rendered answer inline.  Devin resolves a
        # relative markdown link against file:/// (dead), and an absolute
        # file:// URL is inert — the clickable form is a ref_file chip.
        lines += ["", disp]
        if files:
            lines += ["", "files: " + ", ".join(files)]
        lines += [
            "",
            f"Полный ответ → {link}",
            BYPASS_HINT,
        ]
        return "\n".join(lines)

    lines.append(f"A: {_preview_lines(disp, MAX_BODY_PREVIEW)}")
    if files:
        lines.append("files: " + ", ".join(files))
    lines += [
        "",
        f"Полный ответ → {link}",
        BYPASS_HINT,
    ]
    return "\n".join(lines)
