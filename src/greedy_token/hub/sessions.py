from __future__ import annotations

from datetime import UTC, datetime

from greedy_token.hub.paths import sessions_dir
from greedy_token.usage import (
    SESSION_KEYS,
    count_operations,
    load_events,
    log_path,
    parse_since,
)


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    except ValueError:
        return None


def _event_session_id(event: dict) -> str:
    for key in SESSION_KEYS:
        raw = event.get(key)
        if isinstance(raw, str) and raw:
            return raw
    tags = event.get("tags")
    if isinstance(tags, dict):
        for key in SESSION_KEYS:
            raw = tags.get(key)
            if isinstance(raw, str) and raw:
                return raw
    return ""


def list_sessions(*, since: str | None = "7d") -> list[dict]:
    since_dt = parse_since(since) if since else None
    events, _ = load_events(log_path(), since=since_dt)
    session_dir = sessions_dir()
    starts: list[tuple[str, str, datetime]] = []

    if session_dir.is_dir():
        for path in session_dir.glob("*.since"):
            session_id = path.stem
            since_raw = path.read_text(encoding="utf-8").strip()
            since_ts = _parse_ts(since_raw)
            if since_ts is None:
                continue
            if since_dt and since_ts < since_dt:
                continue
            starts.append((session_id, since_raw, since_ts))
    # Oldest first — a session's implicit window ends where the next begins.
    starts.sort(key=lambda row: row[2])

    sessions: list[dict] = []
    for index, (session_id, since_raw, since_ts) in enumerate(starts):
        until = starts[index + 1][2] if index + 1 < len(starts) else None
        bucket = _aggregate(events, session_id=session_id, since=since_ts, until=until)
        sessions.append(
            {
                "session_id": session_id,
                "since": since_raw,
                "calls": bucket["calls"],
                "saved_vs_cursor": bucket["saved_vs_cursor"],
                "est_tokens": bucket["est_tokens"],
            }
        )
    # Newest session first — matches the dashboard's expectation.
    sessions.reverse()

    if not sessions and events:
        bucket = _aggregate_window(events, since=since_dt)
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


def _aggregate(
    events: list[dict],
    *,
    session_id: str,
    since: datetime,
    until: datetime | None,
) -> dict:
    """Events belonging to one session.

    Events carrying a session id are attributed by id. Legacy events without
    one fall back to the implicit window [since, until) — a later session's
    traffic must not inflate an earlier session's bucket.
    """
    filtered = []
    saved = spent = 0
    for event in events:
        event_sid = _event_session_id(event)
        if event_sid:
            if event_sid != session_id:
                continue
        else:
            ts = _parse_ts(event.get("ts", ""))
            if ts is None or ts < since:
                continue
            if until is not None and ts >= until:
                continue
        filtered.append(event)
        saved += int(event.get("cursor_saved") or 0)
        spent += int(event.get("est_tokens") or 0)
    return {
        "calls": count_operations(filtered),
        "saved_vs_cursor": saved,
        "est_tokens": spent,
    }


def _aggregate_window(events: list[dict], *, since: datetime | None) -> dict:
    # "calls" counts operations, not records — a request and its outcome are one.
    filtered = []
    saved = spent = 0
    for event in events:
        ts = _parse_ts(event.get("ts", ""))
        if since and ts and ts < since:
            continue
        filtered.append(event)
        saved += int(event.get("cursor_saved") or 0)
        spent += int(event.get("est_tokens") or 0)
    return {
        "calls": count_operations(filtered),
        "saved_vs_cursor": saved,
        "est_tokens": spent,
    }
