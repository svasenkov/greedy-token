"""Terminal advisory log + watch for beforeSubmitPrompt hook decisions."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


def advisory_log_path() -> Path:
    raw = os.environ.get("GREEDY_ADVISORY_LOG", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_ADVISORY_LOG


def advisory_enabled() -> bool:
    raw = os.environ.get("GREEDY_ADVISORY", "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def overkill_gate_enabled() -> bool:
    raw = os.environ.get("GREEDY_OVERKILL_GATE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def overkill_attachment_threshold() -> int:
    raw = os.environ.get("GREEDY_OVERKILL_ATTACHMENTS", "3").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 3


def tty_path() -> Path | None:
    raw = os.environ.get("GREEDY_TOKEN_TTY", "").strip()
    if not raw:
        return None
    return Path(raw)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    with path.open("a", encoding="utf-8") as fh:
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
    recs = overkill_recommendations(
        prompt=prompt,
        attachment_count=attachment_count,
        est_tokens=est_tokens,
        route_id=route_id,
    )
    body = "\n".join(f"· {r}" for r in recs)
    # Cursor "blocked by hook" toast does not scroll — never echo full prompt.
    preview = _truncate(prompt, TASK_MAX_LEN)
    return (
        "greedy-token: Agent overkill — отправка остановлена\n\n"
        f"Задача: {preview}\n\n"
        f"{body}\n\n"
        "---\n"
        "Agent всё равно нужен → cursor: <промпт>"
    )


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

    def drain() -> None:
        nonlocal seen_pos
        if not path.is_file():
            return
        size = path.stat().st_size
        if size < seen_pos:
            seen_pos = 0
        if size <= seen_pos:
            return
        with path.open(encoding="utf-8") as fh:
            fh.seek(seen_pos)
            chunk = fh.read()
            seen_pos = fh.tell()
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if json_out:
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
