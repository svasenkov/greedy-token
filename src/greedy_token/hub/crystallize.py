from __future__ import annotations

import getpass
import json
import os
import re
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from greedy_token.crystal_ids import crystal_id_for_pattern, stem_of, validate_route_id
from greedy_token.hub.paths import inbox_path, lifecycle_path, watch_state_path
from greedy_token.usage import load_events, log_path, parse_since

SCRIPT_TIERS = frozenset({"tool", "python", "script", "rag"})
LLM_TIERS = frozenset({"ollama", "cursor"})
# Pipeline/pytest dogfood logs tasks as "step :: layer" (audit :: audit).
INBOX_MAX_AGE = timedelta(days=7)
HIDDEN_STATUSES = frozenset({"reject", "rejected"})
# Shared ~/.greedy-token log: lesson folders + workshop task stems.
LESSON_ROOT_MARKERS = (
    "greedy-guru-lesson",
    "greedy-token-workshop",
    "greedy-token-ladder",
)
LESSON_TASK_STEMS = (
    "lab/users.json",
    "llm invoke",
    "check users keys",
    "l6 cloud ollama",
)
_ID_EMAIL = re.compile(r"id.{0,12}[еe]mail", re.IGNORECASE)

# Derived lifecycle states (auditable funnel — never auto-applied):
#   unknown → candidate → proposed → approved → applied
#   rejected is terminal for a proposal; a fresh draft re-proposes it.
STATE_UNKNOWN = "unknown"
STATE_CANDIDATE = "candidate"
STATE_PROPOSED = "proposed"
STATE_APPROVED = "approved"
STATE_APPLIED = "applied"
STATE_REJECTED = "rejected"
# Observational stages — they mark candidacy but never move the state back.
_CANDIDATE_STAGES = frozenset({"watch", "report"})
_PROPOSAL_STAGES = frozenset({"draft", "shadow"})
_APPLIED_STAGES = frozenset({"promoted", "applied"})
_APPROVAL_SOURCE_PROMOTE = "crystallize-promote"


def default_actor() -> str:
    """Who performed a lifecycle transition: explicit env, else local user."""
    actor = os.environ.get("GREEDY_TOKEN_ACTOR", "").strip()
    if actor:
        return actor
    for var in ("USER", "LOGNAME", "USERNAME"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "local-cli"


def is_fixture_task(pattern: str) -> bool:
    """True for pytest/pipeline telemetry ('audit :: audit'), not 'Class::method'."""
    return " :: " in (pattern or "")


def is_lesson_root(path: str) -> bool:
    text = (path or "").replace("\\", "/").lower()
    return any(marker in text for marker in LESSON_ROOT_MARKERS)


def is_lesson_task(pattern: str) -> bool:
    """Workshop / first-pair prompts (users.json schema, llm invoke profiles)."""
    text = (pattern or "").strip().lower()
    if not text:
        return False
    if any(stem in text for stem in LESSON_TASK_STEMS):
        return True
    return _ID_EMAIL.search(text) is not None


def crystal_contour(entry: dict) -> str:
    """Split shared-log crystals: lesson vs this workspace."""
    if is_lesson_task(str(entry.get("pattern") or "")):
        return "lesson"
    if is_lesson_root(str(entry.get("draft_path") or "")):
        return "lesson"
    roots = entry.get("roots") or {}
    paths = list(roots) if isinstance(roots, dict) else list(roots or [])
    if any(is_lesson_root(str(path)) for path in paths):
        return "lesson"
    return "workspace"


def parse_iso_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def inbox_is_fresh(
    inbox: dict,
    *,
    since_dt: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Merge inbox only when updated_at is parseable, ≤7d old, and inside `since`."""
    now = now or datetime.now(UTC)
    updated = parse_iso_ts(inbox.get("updated_at"))
    if updated is None:
        return False
    if now - updated > INBOX_MAX_AGE:
        return False
    if since_dt is not None and updated < since_dt:
        return False
    return True


def rank_candidates(
    *,
    since: str | None = "7d",
    top: int = 15,
    project: str | None = None,
    step: str | None = None,
) -> dict:
    since_dt = parse_since(since) if since else None
    events, _ = load_events(log_path(), since=since_dt)
    if project or step:
        events = [e for e in events if _row_matches_tags(e, project=project, step=step)]
    if not events:
        return {
            "ok": True,
            "coverage_pct": 0.0,
            "total_events": 0,
            "script_or_tool_events": 0,
            "tier_counts": {},
            "candidates": [],
            "fixture_skipped": 0,
            "since": since,
        }

    tier_counts = Counter(e.get("selected_tier", "unknown") for e in events)
    script_like = sum(tier_counts.get(t, 0) for t in SCRIPT_TIERS)
    coverage_pct = round(100.0 * script_like / len(events), 1)

    llm_tasks: Counter[str] = Counter()
    fixture_tasks: set[str] = set()
    task_roots: dict[str, Counter[str]] = defaultdict(Counter)
    seen_llm_ops: set[str] = set()
    for row in events:
        tier = row.get("selected_tier", "")
        if tier not in LLM_TIERS:
            continue
        task = (row.get("task") or "").strip().lower()
        if len(task) < 8:
            continue
        if is_fixture_task(task):
            fixture_tasks.add(task)
            continue
        op_id = str(row.get("operation_id") or "")
        if op_id:
            # One invoke operation = one task hit, however many provider
            # calls its escalation chain logged.
            if op_id in seen_llm_ops:
                continue
            seen_llm_ops.add(op_id)
        llm_tasks[task] += 1
        root = str(row.get("root") or "")
        if root:
            task_roots[task][root] += 1
    fixture_skipped = len(fixture_tasks)

    candidates = []
    for task, hits in llm_tasks.most_common(top):
        cid = crystal_id_for_pattern(task)
        if validate_route_id(cid) is not None:
            continue
        candidates.append(
            {
                "pattern": task,
                "hits": hits,
                "suggested_script": cid,
                "crystal_id": cid,
                "stem": stem_of(cid),
                "tier_seen": "cursor/ollama",
                "roots": dict(task_roots[task]),
            }
        )

    return {
        "ok": True,
        "coverage_pct": coverage_pct,
        "total_events": len(events),
        "script_or_tool_events": script_like,
        "tier_counts": dict(tier_counts),
        "candidates": candidates,
        "fixture_skipped": fixture_skipped,
        "since": since,
    }


def _row_matches_tags(row: dict, *, project: str | None, step: str | None) -> bool:
    if not project and not step:
        return True
    tags = row.get("tags") or {}
    if project and tags.get("project") != project:
        return False
    if step and tags.get("step") != step:
        return False
    return True


def load_json_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _now_iso() -> str:
    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def append_lifecycle_event(
    *,
    stage: str,
    crystal_id: str,
    pattern: str = "",
    hits: int = 0,
    status: str = "pending",
    actor: str = "",
    reason: str = "",
    extra: dict | None = None,
) -> dict:
    """Append a lifecycle stage event (draft/shadow/approved/promoted/rejected/…).

    ``actor``/``reason`` are the audit who/why of the transition; ``ts`` is
    the when. They land as top-level fields so the jsonl stays greppable.
    """
    event: dict = {
        "v": 1,
        "event_id": str(uuid.uuid4()),
        "crystal_id": crystal_id,
        "stage": stage,
        "ts": _now_iso(),
        "pattern": pattern,
        "hits": hits,
        "status": status,
    }
    if actor:
        event["actor"] = actor
    if reason:
        event["reason"] = reason
    if extra:
        event.update(extra)
    path = lifecycle_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def load_lifecycle_events() -> list[dict]:
    path = lifecycle_path()
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def crystal_states(events: list[dict] | None = None) -> dict[str, dict]:
    """Latest derived state per crystal_id — one pass over the lifecycle log.

    The log is append-only audit truth; state is *derived*, never stored:
    watch/report mark candidacy, draft/shadow propose, ``approved`` records a
    human decision (actor/reason/sha pin), promoted/applied is the apply
    transition, rejected is terminal until a fresh draft re-proposes.
    """
    by_id: dict[str, list[dict]] = defaultdict(list)
    rows = load_lifecycle_events() if events is None else events
    for event in rows:
        cid = str(event.get("crystal_id") or "")
        if cid:
            by_id[cid].append(event)
    states: dict[str, dict] = {}
    for cid, evs in by_id.items():
        evs.sort(key=lambda e: str(e.get("ts") or ""))
        state = STATE_UNKNOWN
        approved: dict | None = None
        for event in evs:
            stage = str(event.get("stage") or "")
            if stage in _CANDIDATE_STAGES:
                if state in (STATE_UNKNOWN, STATE_CANDIDATE):
                    state = STATE_CANDIDATE
            elif stage in _PROPOSAL_STAGES:
                state = STATE_PROPOSED
                approved = None  # a fresh draft invalidates the old pin
            elif stage == "approved":
                state = STATE_APPROVED
                approved = event
            elif stage in _APPLIED_STAGES:
                state = STATE_APPLIED
            elif stage == "rejected":
                state = STATE_REJECTED
                approved = None
            # other stages (extract/register/route/smoke) — audit only.
        states[cid] = {
            "state": state,
            "latest_stage": evs[-1].get("stage"),
            "latest_ts": evs[-1].get("ts"),
            "approved": approved,
        }
    return states


def derive_crystal_state(crystal_id: str, events: list[dict] | None = None) -> dict:
    """Derived state for one crystal: {state, latest_stage, latest_ts, approved}."""
    return crystal_states(events).get(
        crystal_id,
        {
            "state": STATE_UNKNOWN,
            "latest_stage": None,
            "latest_ts": None,
            "approved": None,
        },
    )


def savings_by_route(*, since: str | None = "7d") -> list[dict]:
    since_dt = parse_since(since) if since else None
    events, _ = load_events(log_path(), since=since_dt)
    by_route: dict[str, dict] = {}
    for event in events:
        route_id = event.get("route_id", "unknown")
        bucket = by_route.setdefault(
            route_id,
            {"route_id": route_id, "count": 0, "saved_vs_cursor": 0, "est_tokens": 0},
        )
        bucket["count"] += 1
        bucket["saved_vs_cursor"] += int(event.get("cursor_saved") or 0)
        bucket["est_tokens"] += int(event.get("est_tokens") or 0)
    return sorted(by_route.values(), key=lambda x: (-x["saved_vs_cursor"], x["route_id"]))


def crystal_timeline(crystal_id: str) -> dict:
    events = [e for e in load_lifecycle_events() if e.get("crystal_id") == crystal_id]
    events.sort(key=lambda e: e.get("ts", ""))
    stages = {e.get("stage"): e for e in events if e.get("stage")}
    return {
        "crystal_id": crystal_id,
        "events": events,
        "stages": stages,
        "latest_stage": events[-1].get("stage") if events else None,
        "state": derive_crystal_state(crystal_id, events)["state"],
    }


def list_crystals(*, since: str | None = "7d", include_hidden: bool = False) -> dict:
    report = rank_candidates(since=since)
    since_dt = parse_since(since) if since else None
    inbox = load_json_file(inbox_path()) or {}
    watch = load_json_file(watch_state_path()) or {}
    lifecycle = load_lifecycle_events()
    inbox_fresh = inbox_is_fresh(inbox, since_dt=since_dt)

    crystals: dict[str, dict] = {}
    for item in report.get("candidates", []):
        cid = item.get("crystal_id") or item.get("suggested_script")
        crystals[cid] = {
            "crystal_id": cid,
            "stem": stem_of(cid),
            "pattern": item["pattern"],
            "hits": item["hits"],
            "suggested_script": item["suggested_script"],
            "source": "report",
            "latest_stage": "report",
            "roots": item.get("roots") or {},
        }

    if inbox_fresh:
        for item in inbox.get("new_candidates", []):
            cid = crystal_id_for_pattern(item["pattern"])
            entry = crystals.setdefault(
                cid,
                {
                    "crystal_id": cid,
                    "stem": stem_of(cid),
                    "pattern": item["pattern"],
                    "hits": item["hits"],
                    "suggested_script": item.get("suggested_script", cid),
                    "source": "inbox",
                },
            )
            entry["latest_stage"] = "watch"
            entry["inbox_at"] = inbox.get("updated_at")

    for event in lifecycle:
        cid = event.get("crystal_id", "")
        if not cid:
            continue
        ts = parse_iso_ts(event.get("ts"))
        if since_dt is not None and ts is not None and ts < since_dt:
            continue
        entry = crystals.setdefault(
            cid,
            {
                "crystal_id": cid,
                "stem": stem_of(cid),
                "pattern": event.get("pattern", cid),
                "hits": event.get("hits", 0),
                "suggested_script": cid,
                "source": "lifecycle",
            },
        )
        stage = event.get("stage")
        if stage:
            entry["latest_stage"] = stage
        if event.get("status"):
            entry["status"] = event["status"]
        if event.get("draft_path"):
            entry["draft_path"] = event["draft_path"]

    hidden_reject = 0
    hidden_fixture = int(report.get("fixture_skipped") or 0)
    visible: list[dict] = []
    for entry in crystals.values():
        status = str(entry.get("status") or "").strip().lower()
        if status in HIDDEN_STATUSES:
            hidden_reject += 1
            continue
        if is_fixture_task(str(entry.get("pattern") or "")):
            hidden_fixture += 1
            continue
        visible.append(entry)

    notified = watch.get("notified") or {}
    states = crystal_states(lifecycle)
    shown = list(crystals.values()) if include_hidden else visible
    workspace: list[dict] = []
    lesson: list[dict] = []
    for entry in shown:
        entry["stem"] = entry.get("stem") or stem_of(entry["crystal_id"])
        # Derived lifecycle state; report/inbox-only rows are bare candidates.
        entry["state"] = states.get(entry["crystal_id"], {}).get(
            "state"
        ) or STATE_CANDIDATE
        contour = crystal_contour(entry)
        entry["contour"] = contour
        if contour == "lesson":
            lesson.append(entry)
        else:
            workspace.append(entry)

    def sort_key(row: dict) -> tuple:
        return (-row.get("hits", 0), row["crystal_id"])

    return {
        "coverage_pct": report.get("coverage_pct"),
        "total_events": report.get("total_events"),
        "crystals": sorted(workspace, key=sort_key),
        "lesson": sorted(lesson, key=sort_key),
        "notified_patterns": list(notified.keys()),
        "hidden": {
            "count": hidden_reject + hidden_fixture,
            "reject": hidden_reject,
            "fixture": hidden_fixture,
            "stale_inbox": not inbox_fresh and bool(inbox),
        },
        "since": since,
    }
