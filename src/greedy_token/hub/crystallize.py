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
from greedy_token.paths import find_workspace_root
from greedy_token.usage import (
    CHEAP_TIERS,
    load_events,
    log_path,
    normalize_task,
    parse_since,
)

SCRIPT_TIERS = frozenset({"tool", "python", "script", "rag"})
LLM_TIERS = frozenset({"ollama", "cursor"})
OVERRIDE_EVENT = "script_override"
PROMOTE_MIN_HITS = 3
# Sessionization fallback: usage events carry no session_id, so >30min of
# silence between any events splits a work session (web-analytics convention).
SESSION_GAP_SEC = 30 * 60
NOISE_EXACT = frozenset(
    {
        "audit :: audit",
        "cursor :: cursor",
        "audit-skill x :: audit",
        "classify-file x :: classify",
        "audit-skill gap :: audit",
        "classify-file gap :: classify",
    }
)
# pytest pipeline fixtures for pipeline-audit-skill / pipeline-classify-file
_PIPELINE_FIXTURE_TASK = re.compile(
    r"^(audit-skill|classify-file) [a-z0-9_-]+ :: (audit|classify)$"
)
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
# Sandbox/test roots — tmp dirs and pytest tmp_path are fixture telemetry,
# not real repeated work (same class as `is_fixture_task`).
_SANDBOX_ROOT_RE = re.compile(
    r"^/(?:private/)?(?:tmp|var/folders)/|[/\\]temp[/\\]pytest",
    re.IGNORECASE,
)

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


def is_noise(pattern: str) -> bool:
    """True for patterns that are telemetry noise or too vague to crystallize."""
    p = (pattern or "").lower().strip()
    if p in NOISE_EXACT:
        return True
    if _PIPELINE_FIXTURE_TASK.fullmatch(p):
        return True
    if len(p) < 12:
        return True
    if re.fullmatch(r"[a-z]+ :: [a-z]+", p):
        return True
    return False


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


def is_sandbox_roots(roots: object) -> bool:
    """True when every observed root is a tmp/pytest sandbox — fixture noise."""
    if isinstance(roots, dict):
        paths = [p for p in roots if p]
    else:
        paths = [str(p) for p in (roots or []) if p]
    return bool(paths) and all(
        _SANDBOX_ROOT_RE.search(str(path)) is not None for path in paths
    )


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


def _session_id(row: dict) -> str | None:
    tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
    for key in ("session_id", "session", "sid"):
        val = row.get(key) or tags.get(key)
        if val:
            return str(val)
    return None


def _event_day(row: dict) -> str | None:
    when = parse_iso_ts(row.get("ts"))
    return when.date().isoformat() if when else None


def _gap_sessions(rows: list[dict]) -> dict[int, int]:
    """Map id(row) -> session index from time gaps across ALL activity."""
    timed = sorted(
        (ts, id(row)) for row in rows if (ts := parse_iso_ts(row.get("ts"))) is not None
    )
    buckets: dict[int, int] = {}
    idx = 0
    prev: datetime | None = None
    for ts, row_id in timed:
        if prev is not None and (ts - prev).total_seconds() > SESSION_GAP_SEC:
            idx += 1
        buckets[row_id] = idx
        prev = ts
    return buckets


def _row_session(row: dict, gap_sessions: dict[int, int]) -> str | None:
    explicit = _session_id(row)
    if explicit:
        return explicit
    idx = gap_sessions.get(id(row))
    return f"gap-{idx}" if idx is not None else None


def script_hits_by_route(route_rows: list[dict]) -> dict[str, int]:
    """Count cheap-tier hits per route/crystal id (override_rate denominator)."""
    hits: Counter[str] = Counter()
    for row in route_rows:
        if row.get("selected_tier") in CHEAP_TIERS:
            hits[row.get("route_id") or "unknown"] += 1
    return dict(hits)


def rank_overrides(
    rows: list[dict],
    top: int,
    *,
    script_hits_by_crystal: dict[str, int] | None = None,
) -> list[dict]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        crystal_id = row.get("crystal_id") or row.get("route_id") or "unknown"
        task = normalize_task(row.get("task_normalized") or row.get("task") or "")
        counts[(crystal_id, task)] += 1
    out: list[dict] = []
    for (crystal_id, task), count in counts.most_common(top):
        entry: dict = {
            "crystal_id": crystal_id,
            "task": task,
            "override_count": count,
        }
        if script_hits_by_crystal is not None:
            hits = script_hits_by_crystal.get(crystal_id, 0)
            entry["script_hits"] = hits
            entry["override_rate"] = round(count / max(1, hits), 4)
        out.append(entry)
    return out


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


def covered_route_id(task: str, root: Path | None = None) -> str | None:
    """Active non-cursor route already matching ``task``, else None.

    Fail-open: a missing workspace, a broken overlay, or any router error
    returns None — coverage suppression must never hide evidence on failure.
    Lazy router import keeps the hub free of router→usage import cycles.
    """
    try:
        root = find_workspace_root() if root is None else Path(root)
        from greedy_token.router import route_task_all_tiers

        for tier, decision in route_task_all_tiers(task, root):
            if tier != "cursor" and decision.matched:
                return decision.route_id
    except (Exception, SystemExit):
        return None
    return None


def rank_candidates(
    *,
    since: str | None = "7d",
    top: int = 15,
    project: str | None = None,
    step: str | None = None,
    root: Path | None = None,
    usage_path: Path | None = None,
    include_overrides: bool = False,
) -> dict:
    """Rank LLM-tier tasks as crystallize candidates — the single SSOT.

    Canonical hit semantics (scripts/_crystallize_lib.py delegates here):

    - one hit = one request event on an LLM tier (``ollama``/``cursor``);
      ``route_outcome`` rows are the same operation's result, never a new hit;
    - ``script_override`` rows are excluded from hits and tier_counts but
      measured separately (``override_rate`` over cheap-tier hits);
    - rows sharing ``operation_id`` count once — an escalation chain is one
      task invocation, however many provider calls it logged;
    - candidates merge by canonical ``crystal_id_for_pattern`` (no fuzzy
      matching — the canonical id already covers spelling variants);
    - ``promote_ready`` = ≥3 hits across ≥2 sessions or ≥2 distinct days;
    - tasks matching an active non-cursor route move to ``covered`` (an
      adoption gap, not a missing primitive); tmp/pytest-rooted tasks are
      sandbox noise unless the workspace itself is a sandbox.
    """
    since_dt = parse_since(since) if since else None
    path = Path(usage_path) if usage_path is not None else log_path()
    events, _skipped = load_events(path, since=since_dt)
    if project or step:
        events = [e for e in events if _row_matches_tags(e, project=project, step=step)]
    if not events:
        return {
            "ok": True,
            "coverage_pct": 0.0,
            "total_events": 0,
            "script_or_tool_events": 0,
            "script_override_events": 0,
            "override_rate": 0.0,
            "cheap_hold_rate": 1.0,
            "tier_counts": {},
            "candidates": [],
            "covered": [],
            "fixture_skipped": 0,
            "sandbox_skipped": 0,
            "usage_path": str(path),
            "since": since,
            "project": project,
            "step": step,
        }

    # Overrides are telemetry about already-routed work, not candidate
    # material — excluded from hits and tier_counts, measured on their own.
    override_rows = [e for e in events if e.get("event") == OVERRIDE_EVENT]
    route_rows = [e for e in events if e.get("event") != OVERRIDE_EVENT]
    tier_counts = Counter(e.get("selected_tier", "unknown") for e in route_rows)
    script_like = sum(tier_counts.get(t, 0) for t in SCRIPT_TIERS)
    coverage_pct = round(100.0 * script_like / len(events), 1)

    gap_sessions = _gap_sessions(route_rows)
    llm_tasks: Counter[str] = Counter()
    fixture_tasks: set[str] = set()
    task_roots: dict[str, Counter[str]] = defaultdict(Counter)
    task_sessions: dict[str, set[str]] = defaultdict(set)
    task_days: dict[str, set[str]] = defaultdict(set)
    seen_llm_ops: set[str] = set()
    for row in route_rows:
        tier = row.get("selected_tier", "")
        if tier not in LLM_TIERS:
            continue
        if row.get("event") == "route_outcome":
            continue  # outcome of an already-counted request, never a new hit
        task = normalize_task(row.get("task_normalized") or row.get("task") or "")
        if len(task) < 8:
            continue
        if is_fixture_task(task) or is_noise(task):
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
        event_root = str(row.get("root") or "")
        if event_root:
            task_roots[task][event_root] += 1
        session = _row_session(row, gap_sessions)
        if session:
            task_sessions[task].add(session)
        day = _event_day(row)
        if day:
            task_days[task].add(day)
    fixture_skipped = len(fixture_tasks)

    # One candidate per canonical id — spellings that derive the same stem
    # merge (hits and roots) instead of duplicating rows in the listing.
    merged: dict[str, dict] = {}
    for task, hits in llm_tasks.most_common():
        cid = crystal_id_for_pattern(task)
        if validate_route_id(cid) is not None:
            continue
        row = merged.get(cid)
        if row is None:
            row = merged[cid] = {
                "pattern": task,
                "hits": 0,
                "suggested_script": cid,
                "crystal_id": cid,
                "stem": stem_of(cid),
                "tier_seen": "cursor/ollama",
                "roots": Counter(),
                "_sessions": set(),
                "_days": set(),
            }
        row["hits"] += hits
        row["roots"].update(task_roots[task])
        row["_sessions"].update(task_sessions[task])
        row["_days"].update(task_days[task])

    # Split coverage: a task that already matches an active non-cursor route
    # is an adoption gap (agent never invoked it), not a missing primitive —
    # it moves to `covered`, keeping `candidates` for genuinely new work.
    # Tasks seen only from tmp/pytest sandboxes are fixture noise, not work —
    # unless the workspace itself is a sandbox (test envs live in tmp).
    ws_root: Path | None = root
    if ws_root is None:
        try:
            ws_root = find_workspace_root()
        except SystemExit:
            ws_root = None
    ws_is_sandbox = bool(
        ws_root is not None
        and _SANDBOX_ROOT_RE.search(str(ws_root)) is not None
    )
    candidates: list[dict] = []
    covered: list[dict] = []
    sandbox_skipped = 0
    ordered = sorted(merged.values(), key=lambda c: (-c["hits"], c["crystal_id"]))
    for row in ordered:
        if not ws_is_sandbox and is_sandbox_roots(row["roots"]):
            sandbox_skipped += 1
            continue
        route_id = covered_route_id(row["pattern"], ws_root)
        if route_id:
            row["covered_by"] = route_id
            covered.append(row)
        else:
            candidates.append(row)
    candidates = candidates[:top]
    covered = covered[:top]
    for row in candidates + covered:
        sessions = row.pop("_sessions")
        days = row.pop("_days")
        row["roots"] = dict(row["roots"])
        row["distinct_sessions"] = len(sessions)
        row["distinct_days"] = len(days)
        row["promote_ready"] = bool(
            int(row["hits"]) >= PROMOTE_MIN_HITS
            and (row["distinct_sessions"] >= 2 or row["distinct_days"] >= 2)
        )

    script_hits_by_crystal = script_hits_by_route(route_rows)
    script_hits_total = sum(script_hits_by_crystal.values())
    override_rate = round(len(override_rows) / max(1, script_hits_total), 4)

    result = {
        "ok": True,
        "coverage_pct": coverage_pct,
        "total_events": len(events),
        "script_or_tool_events": script_like,
        "script_override_events": len(override_rows),
        "override_rate": override_rate,
        "cheap_hold_rate": round(max(0.0, 1.0 - override_rate), 4),
        "tier_counts": dict(tier_counts),
        "candidates": candidates,
        "covered": covered,
        "fixture_skipped": fixture_skipped,
        "sandbox_skipped": sandbox_skipped,
        "usage_path": str(path),
        "since": since,
        "project": project,
        "step": step,
    }
    if include_overrides:
        result["overrides"] = rank_overrides(
            override_rows, top, script_hits_by_crystal=script_hits_by_crystal
        )
    return result


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
            if covered_route_id(item["pattern"]):
                continue
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
    hidden_sandbox = int(report.get("sandbox_skipped") or 0)
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

    covered = [
        {
            "crystal_id": item.get("crystal_id"),
            "stem": item.get("stem"),
            "pattern": item.get("pattern"),
            "hits": item.get("hits", 0),
            "covered_by": item.get("covered_by"),
        }
        for item in report.get("covered") or []
    ]
    return {
        "coverage_pct": report.get("coverage_pct"),
        "total_events": report.get("total_events"),
        "crystals": sorted(workspace, key=sort_key),
        "lesson": sorted(lesson, key=sort_key),
        "covered": covered,
        "notified_patterns": list(notified.keys()),
        "hidden": {
            "count": hidden_reject + hidden_fixture + hidden_sandbox,
            "reject": hidden_reject,
            "fixture": hidden_fixture,
            "sandbox": hidden_sandbox,
            "stale_inbox": not inbox_fresh and bool(inbox),
        },
        "since": since,
    }
