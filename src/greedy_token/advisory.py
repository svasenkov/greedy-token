"""Terminal advisory log + watch for beforeSubmitPrompt hook decisions."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_ADVISORY_LOG = Path.home() / ".greedy-token" / "advisory.jsonl"
TASK_MAX_LEN = 400

EDIT_VERBS = re.compile(
    r"\b(implement|refactor|fix|add|wiring|migrate|patch|rewrite|"
    r"почини|исправь|добавь|рефактор|внедри|сделай)\b",
    re.IGNORECASE,
)
QUESTION_HINT = re.compile(
    r"(^|\s)(what|how|why|where|explain|что|как|где|объясни|расскажи|зачем)(\s|$|\?)",
    re.IGNORECASE,
)

KIND_INTERCEPT = "intercept"
KIND_OVERKILL = "overkill"
KIND_PASS = "pass"
KIND_BYPASS = "bypass"
KIND_GATE = "gate"

# Hook submit modes — single source for beforeSubmitPrompt policy:
#   advisory   — log every routed prompt, never block (default).
#   gate       — block prompts matching an *invocable* route; the toast points
#                at `greedy-token capabilities invoke <op>` / MCP invoke — nothing auto-executed.
#   intercept  — run the cheap op and return its output in the blocked toast
#                (full text spills to ~/.greedy-token/last-intercept.md).
HOOK_MODE_ADVISORY = "advisory"
HOOK_MODE_GATE = "gate"
HOOK_MODE_INTERCEPT = "intercept"
HOOK_MODES = frozenset({HOOK_MODE_ADVISORY, HOOK_MODE_GATE, HOOK_MODE_INTERCEPT})

# Advisory default sits above max confidence (1.0): nothing ever blocks.
ADVISORY_MIN_CONFIDENCE = 1.01
# Enforce default: above cursor-fallback (0.35), below real route matches.
ENFORCE_MIN_CONFIDENCE = 0.55


def advisory_log_path() -> Path:
    raw = os.environ.get("GREEDY_ADVISORY_LOG", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_ADVISORY_LOG


def advisory_enabled() -> bool:
    # equivalent: a "XX1XX"-style env-get default is no off-word either — an
    # unset var keeps advisory enabled identically.
    raw = os.environ.get("GREEDY_ADVISORY", "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def overkill_gate_enabled() -> bool:
    # equivalent: a "XXXX" env-get default is no on-word either — an unset
    # var keeps the gate disabled identically.
    raw = os.environ.get("GREEDY_OVERKILL_GATE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def overkill_attachment_threshold() -> int:
    # equivalent: a "XX3XX" env-get default fails int() → the same except
    # branch returns 3 either way.
    raw = os.environ.get("GREEDY_OVERKILL_ATTACHMENTS", "3").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 3


def _hook_settings():
    """hook: section from config yaml; any failure → unconfigured (legacy)."""
    from greedy_token.settings import get_hook_settings

    try:
        return get_hook_settings()
    except Exception:
        return None


def hook_mode() -> str:
    """Hook mode profile; unset everywhere → legacy (threshold env only).

    GREEDY_HOOK_MODE env wins; `hook.mode` yaml is the durable opt-in.
    Junk values fail safe to advisory — a typo must never start blocking.
    """
    raw = os.environ.get("GREEDY_HOOK_MODE", "").strip().lower()
    if raw:
        return raw if raw in HOOK_MODES else HOOK_MODE_ADVISORY
    cfg = _hook_settings()
    return cfg.mode if cfg is not None and cfg.mode else ""


def hook_min_confidence() -> float:
    """Effective block threshold for the active hook mode.

    An explicit GREEDY_HOOK_MIN_CONFIDENCE always wins in the enforce modes,
    then `hook.min_confidence` yaml; MODE=advisory never blocks, whatever
    either source says.  With no mode and no value anywhere the legacy
    default keeps everything advisory.
    """
    mode = hook_mode()
    if mode == HOOK_MODE_ADVISORY:
        return ADVISORY_MIN_CONFIDENCE
    # equivalent: a "XXXX" env-get default is truthy but fails float() → the
    # same except-pass → enforce/legacy defaults apply identically.
    raw = os.environ.get("GREEDY_HOOK_MIN_CONFIDENCE", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    cfg = _hook_settings()
    if cfg is not None and cfg.min_confidence is not None:
        return cfg.min_confidence
    if mode in (HOOK_MODE_GATE, HOOK_MODE_INTERCEPT):
        return ENFORCE_MIN_CONFIDENCE
    return ADVISORY_MIN_CONFIDENCE


def tty_path() -> Path | None:
    raw = os.environ.get("GREEDY_TOKEN_TTY", "").strip()
    if not raw:
        return None
    return Path(raw)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _truncate(text: str, limit: int = TASK_MAX_LEN) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def parse_attachments(data: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for item in data.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        path = item.get("file_path") or item.get("path") or ""
        if path:
            paths.append(str(path))
    return paths


def is_question_like(prompt: str) -> bool:
    return bool(QUESTION_HINT.search(prompt)) and not EDIT_VERBS.search(prompt)


def is_overkill(
    prompt: str,
    *,
    route_id: str,
    target: str,
    attachment_count: int,
) -> bool:
    if target != "cursor":
        return False
    # equivalent: mutating this early return (==/XX-literals) is unobservable
    # — an EDIT_VERBS prompt is never question-like, so the next check returns
    # the same False on every path.
    if route_id != "cursor-fallback" and EDIT_VERBS.search(prompt):
        return False
    if not is_question_like(prompt):
        return False
    threshold = overkill_attachment_threshold()
    if threshold == 0:
        return route_id == "cursor-fallback"
    return attachment_count >= threshold or (
        route_id == "cursor-fallback" and attachment_count > 0
    )


def overkill_recommendations(
    *,
    prompt: str,
    attachment_count: int,
    est_tokens: int,
    route_id: str,
) -> list[str]:
    lines: list[str] = [
        f"Agent overkill (~{est_tokens:,} tokens with rules context).",
        f"Route: {route_id}.",
    ]
    if attachment_count:
        lines.append(f"Attachments: {attachment_count} — открепите или pin 1–3 файла.")
    lines.extend(
        [
            "Shift+Tab → Ask (вопрос без правок)",
            "Переформулировать: find … / объясни … → hook перехватит",
            "Префикс ask: — read-only в Agent",
            "Нужен полный Agent → cursor: <промпт>",
        ]
    )
    return lines


@dataclass
class AdvisoryEvent:
    ts: str
    kind: str
    action: str
    prompt: str
    target: str
    route_id: str
    confidence: float
    est_tokens: int
    attachment_count: int = 0
    attachments: list[str] = field(default_factory=list)
    session_id: str | None = None
    composer_mode: str | None = None
    recommendations: list[str] = field(default_factory=list)
    blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_event(event: AdvisoryEvent) -> None:
    if not advisory_enabled():
        return
    path = advisory_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" pins LF bytes — text mode would translate to CRLF on Windows.
    # equivalent: newline=None/dropped selects default newline handling, which
    # on POSIX writes the same LF bytes the write below already produces.
    with path.open("a", encoding="utf-8", newline="") as fh:
        # equivalent: ensure_ascii=None is falsy like False → the same
        # non-escaping path; truthy mutations are killed by the bytes test.
        fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def write_tty(event: AdvisoryEvent) -> None:
    tty = tty_path()
    if tty is None:
        return
    try:
        with tty.open("w", encoding="utf-8") as fh:
            fh.write(format_terminal_block(event))
            fh.flush()
    except OSError:
        pass


def emit_advisory(event: AdvisoryEvent) -> None:
    append_event(event)
    write_tty(event)


def build_event(
    *,
    kind: str,
    action: str,
    prompt: str,
    decision: Any,
    data: dict[str, Any],
    blocked: bool = False,
    recommendations: list[str] | None = None,
) -> AdvisoryEvent:
    attachments = parse_attachments(data)
    return AdvisoryEvent(
        ts=_utc_now_iso(),
        kind=kind,
        action=action,
        prompt=_truncate(prompt),
        target=getattr(decision, "target", "cursor"),
        route_id=getattr(decision, "route_id", ""),
        confidence=float(getattr(decision, "confidence", 0)),
        est_tokens=int(getattr(decision, "est_tokens", 0)),
        attachment_count=len(attachments),
        attachments=attachments[:8],
        session_id=data.get("session_id") or data.get("conversation_id"),
        composer_mode=data.get("composer_mode"),
        recommendations=recommendations or [],
        blocked=blocked,
    )


def format_terminal_block(event: AdvisoryEvent) -> str:
    header = {
        KIND_INTERCEPT: "INTERCEPT (cheap tier)",
        KIND_OVERKILL: "OVERKILL (Agent heavy)",
        KIND_PASS: "PASS (Agent)",
        KIND_BYPASS: "BYPASS (cursor: prefix)",
        KIND_GATE: "GATE (invoke required)",
    }.get(event.kind, event.kind.upper())

    action = "BLOCKED" if event.blocked else event.action.upper()
    lines = [
        "",
        f"\033[36m[greedy-token watch]\033[0m {header} · {action}",
        f"  tier: {event.target.upper()} ({event.route_id}, {event.confidence:.0%})",
        f"  est: ~{event.est_tokens:,} tokens",
    ]
    if event.attachment_count:
        lines.append(f"  attachments: {event.attachment_count}")
    lines.append(f"  prompt: {event.prompt}")
    if event.recommendations:
        lines.append("\033[33m  recommendations:\033[0m")
        for rec in event.recommendations:
            lines.append(f"    · {rec}")
    lines.append("")
    return "\n".join(lines)


def format_overkill_user_message(
    prompt: str,
    *,
    attachment_count: int,
    est_tokens: int,
    route_id: str,
) -> str:
    # equivalent: overkill_recommendations never reads its prompt param —
    # passing None instead is unobservable.
    recs = overkill_recommendations(
        prompt=prompt,
        attachment_count=attachment_count,
        est_tokens=est_tokens,
        route_id=route_id,
    )
    body = "\n".join(f"· {r}" for r in recs)
    # Cursor "blocked by hook" toast does not scroll — never echo full prompt.
    # equivalent: dropping the limit arg restores _truncate's declared
    # default limit=TASK_MAX_LEN — the same call.
    preview = _truncate(prompt, TASK_MAX_LEN)
    return (
        "greedy-token: Agent overkill — отправка остановлена\n\n"
        f"Задача: {preview}\n\n"
        f"{body}\n\n"
        "---\n"
        "Agent всё равно нужен → cursor: <промпт>"
    )


def format_gate_user_message(prompt: str, *, op_id: str) -> str:
    """Blocked toast for gate mode — a trusted op exists; run it, no Agent."""
    # equivalent: a dropped limit arg re-enters _truncate with its own
    # TASK_MAX_LEN default — identical output.
    preview = _truncate(prompt, TASK_MAX_LEN)
    return (
        "greedy-token gate — детерминированный op, отправка остановлена\n\n"
        f"Задача: {preview}\n"
        f"Op: {op_id} (ready · read-only)\n\n"
        f"Запуск: greedy-token capabilities invoke {op_id} · MCP: greedy_token_invoke\n"
        "---\n"
        "Agent всё равно нужен → cursor: <промпт>"
    )


_INTERCEPT_CELL_MAX = 72
_INTERCEPT_ROWS_MAX = 10
_INTERCEPT_LIST_INLINE = 4


def render_result_output(output: str) -> str:
    """Human-readable render of an op's stdout for the intercept toast.

    JSON becomes key/value lines plus a markdown table per array of objects;
    anything else is already human-readable and returned unchanged.
    """
    text = output.strip()
    if not text:
        return text
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, dict):
        return _render_result_dict(data) or text
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        return _render_result_table(data)
    return text


def _render_result_dict(d: dict[str, Any]) -> str:
    scalars = [
        f"**{k}**: {_result_cell(v)}"
        for k, v in d.items()
        if not isinstance(v, (dict, list))
    ]
    blocks = ["\n".join(scalars)] if scalars else []
    for key, value in d.items():
        if isinstance(value, list) and value and all(
            isinstance(r, dict) for r in value
        ):
            blocks.append(f"**{key}**\n\n{_render_result_table(value)}")
        elif isinstance(value, (dict, list)):
            blocks.append(f"**{key}**: {_result_cell(value)}")
    return "\n\n".join(blocks)


def _render_result_table(rows: list[dict[str, Any]]) -> str:
    cols: list[str] = []
    for row in rows:
        cols += [k for k in row if k not in cols]
    lines = [
        "| " + " | ".join(cols) + " |",
        "|" + "---|" * len(cols),
    ]
    lines += [
        "| " + " | ".join(_result_cell(row.get(c)) for c in cols) + " |"
        for row in rows[:_INTERCEPT_ROWS_MAX]
    ]
    if len(rows) > _INTERCEPT_ROWS_MAX:
        lines.append(f"| … +{len(rows) - _INTERCEPT_ROWS_MAX} more" + " |" * (len(cols) - 1))
    return "\n".join(lines)


def _result_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        inline = ", ".join(_result_cell(v) for v in value[:_INTERCEPT_LIST_INLINE])
        if len(value) > _INTERCEPT_LIST_INLINE:
            inline += f" +{len(value) - _INTERCEPT_LIST_INLINE}"
        return _cell_truncate(inline)
    if isinstance(value, dict):
        return _cell_truncate(json.dumps(value, ensure_ascii=False))
    return _cell_truncate(str(value))


def _cell_truncate(text: str) -> str:
    text = text.replace("\n", " ").replace("|", "\\|")
    return _truncate(text, _INTERCEPT_CELL_MAX)


def event_from_dict(row: dict[str, Any]) -> AdvisoryEvent:
    return AdvisoryEvent(
        ts=row.get("ts", ""),
        kind=row.get("kind", ""),
        action=row.get("action", ""),
        prompt=row.get("prompt", ""),
        target=row.get("target", ""),
        route_id=row.get("route_id", ""),
        confidence=float(row.get("confidence", 0)),
        est_tokens=int(row.get("est_tokens", 0)),
        attachment_count=int(row.get("attachment_count", 0)),
        attachments=list(row.get("attachments") or []),
        session_id=row.get("session_id"),
        composer_mode=row.get("composer_mode"),
        recommendations=list(row.get("recommendations") or []),
        # equivalent: bool(None) == bool(missing) == False — the mutated
        # default lands on the same falsy outcome.
        blocked=bool(row.get("blocked", False)),
    )


def watch_events(
    *,
    follow: bool = True,
    from_start: bool = False,
    json_out: bool = False,
) -> int:
    path = advisory_log_path()
    if not path.is_file():
        print(f"Waiting for advisory log: {path}", file=sys.stderr)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    seen_pos = 0 if from_start else path.stat().st_size
    pending = b""  # unconsumed tail of an unfinished JSON line

    def drain() -> None:
        nonlocal seen_pos, pending
        if not path.is_file():
            return
        size = path.stat().st_size
        if size < seen_pos:
            seen_pos = 0
            pending = b""
        # equivalent: `size < seen_pos` instead of <= only differs at
        # size == seen_pos — a re-read then yields b"" so `pending` splits to
        # the same buffered fragment and nothing is emitted either way.
        if size <= seen_pos:
            return
        with path.open("rb") as fh:
            fh.seek(seen_pos)
            chunk = pending + fh.read()
            seen_pos = fh.tell()
        # Only newline-terminated bytes are parsed; the trailing fragment stays
        # buffered so a record written across several writes is never dropped.
        *complete, pending = chunk.split(b"\n")
        for raw in complete:
            # equivalent: a dropped/renamed encoding arg keeps utf-8 —
            # decode() defaults to "utf-8" and codec names are
            # case-insensitive; only errors-handler mutations diverge.
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if json_out:
                # equivalent: ensure_ascii=None is falsy like False → same
                # non-escaping output; truthy mutations die on Cyrillic rows.
                print(json.dumps(row, ensure_ascii=False))
            else:
                sys.stdout.write(format_terminal_block(event_from_dict(row)))
                sys.stdout.flush()

    drain()
    if not follow:
        return 0

    print(
        f"\033[90mwatching {path} — submit prompts in Cursor Agent\033[0m",
        file=sys.stderr,
    )
    try:
        while True:
            time.sleep(0.25)
            drain()
    except KeyboardInterrupt:
        print("\n\033[90mwatch stopped\033[0m", file=sys.stderr)
        return 0
