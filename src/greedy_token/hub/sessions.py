from __future__ import annotations

from datetime import datetime, timezone

from greedy_token.hub.paths import sessions_dir
from greedy_token.usage import load_events, log_path, parse_since


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except ValueError:
        return None


def list_sessions(*, since: str | None = "7d") -> list[dict]:
    since_dt = parse_since(since) if since else None
    events, _ = load_events(log_path(), since=since_dt)
    session_dir = sessions_dir()
    sessions: list[dict] = []

    if session_dir.is_dir():
        for path in sorted(session_dir.glob("*.since"), key=lambda p: p.stat().st_mtime, reverse=True):
            session_id = path.stem
            since_raw = path.read_text(encoding="utf-8").strip()
            since_ts = _parse_ts(since_raw)
            if since_ts is None:
                continue
            if since_dt and since_ts < since_dt:
                continue
            bucket = _aggregate(events, since=since_ts, root=None)
            sessions.append(
                {
                    "session_id": session_id,
                    "since": since_raw,
                    "calls": bucket["calls"],
                    "saved_vs_cursor": bucket["saved_vs_cursor"],
                    "est_tokens": bucket["est_tokens"],
                }
            )

    if not sessions and events:
        bucket = _aggregate(events, since=since_dt, root=None)
        sessions.append(
            {
                "session_id": "all",
                "since": since or "all",
                "calls": bucket["calls"],
                "saved_vs_cursor": bucket["saved_vs_cursor"],
                "est_tokens": bucket["est_tokens"],
            }
        )

    return sessions


def _aggregate(events: list[dict], *, since: datetime | None, root: str | None) -> dict:
    calls = saved = spent = 0
    for event in events:
        ts = _parse_ts(event.get("ts", ""))
        if since and ts and ts < since:
            continue
        if root and event.get("root") != root:
            continue
        calls += 1
        saved += int(event.get("cursor_saved") or 0)
        spent += int(event.get("est_tokens") or 0)
    return {"calls": calls, "saved_vs_cursor": saved, "est_tokens": spent}
