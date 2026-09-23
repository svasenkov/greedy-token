"""Crystallization L3 in safe mode: candidate → proposed → approved → applied.

No silent auto-apply. ``crystallize draft`` generates a reviewable Python
script in ``.greedy-token/drafts/`` (cheap LLM when available, deterministic
template otherwise) and registers a *shadow* route in the workspace config
(``shadow_until`` +7d, ``enabled: false``). A shadow route never changes
``route_task`` — it is log-only. ``crystallize approve`` records the human
decision (who/why + sha256 pin of the reviewed draft, bound to the
workspace); ``crystallize promote`` then applies — the draft passes through
Step-2 trust (``approve_script`` binds the approved bytes in the user-local
manifest, re-verified against the pin at apply time) and the route goes
active. ``reject`` removes the draft, the route, and every trust entry the
candidate ever bound. Every transition appends a lifecycle event that the
hub shows.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from greedy_token.crystal_ids import validate_route_id
from greedy_token.hub.crystallize import (
    _APPROVAL_SOURCE_PROMOTE,
    STATE_APPLIED,
    STATE_APPROVED,
    STATE_CANDIDATE,
    STATE_PROPOSED,
    STATE_REJECTED,
    STATE_UNKNOWN,
    append_lifecycle_event,
    default_actor,
    derive_crystal_state,
    list_crystals,
    load_lifecycle_events,
)
from greedy_token.paths import (
    WORKSPACE_CONFIG_NAME,
    load_routes_config,
    remove_workspace_route,
    upsert_workspace_routes,
    workspace_config_routes,
)
from greedy_token.scripts_lint import (
    extract_script_path,
    lint_routes,
    pattern_violations,
)
from greedy_token.trust import (
    TrustError,
    _workspace_id,
    approve_script,
    revoke_script,
    trusted_manifest_paths,
    verify_trust_manifest,
)

DRAFTS_DIR = Path(".greedy-token") / "drafts"
SHADOW_WINDOW_DAYS = 7

DRAFT_SYSTEM_PROMPT = (
    "You generate small deterministic Python scripts that replace a repeated "
    "LLM task (crystallization). Output a single self-contained Python 3.12 "
    "script and nothing else: argparse CLI, read-only, stdout JSON summary, "
    "no third-party imports. No prose, no markdown fences."
)

_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class DraftResult:
    crystal_id: str
    pattern: str
    hits: int
    draft_path: Path
    config_path: Path
    shadow_until: str
    source: str  # "cheap_llm" | "template"
    lint_ok: bool
    lint_violations: list[dict]


def drafts_dir(root: Path) -> Path:
    return root / DRAFTS_DIR


def draft_path(root: Path, crystal_id: str) -> Path:
    return drafts_dir(root) / f"{crystal_id}.py"


def find_crystal(crystal_id: str, *, since: str | None = "30d") -> dict | None:
    """Candidate metadata (pattern/hits) from report + inbox + lifecycle.

    Includes hub-hidden rows (reject / pytest fixture) so draft/promote after
    reject still resolves the same id.
    """
    listing = list_crystals(since=since, include_hidden=True)
    for pool in (listing.get("crystals"), listing.get("lesson")):
        for crystal in pool or []:
            if crystal.get("crystal_id") == crystal_id:
                return crystal
    return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proposal_meta(crystal_id: str, route: dict | None) -> tuple[str, int]:
    """Pattern/hits for lifecycle events — route patterns first, then the log."""
    pattern = ""
    hits = 0
    if route:
        patterns = route.get("patterns") or []
        if patterns:
            pattern = str(patterns[0])
    for event in reversed(load_lifecycle_events()):
        if event.get("crystal_id") != crystal_id:
            continue
        if not pattern:
            pattern = str(event.get("pattern") or "")
        hits = hits or int(event.get("hits") or 0)
        if pattern and hits:
            break
    return pattern, hits


def _shadow_until_iso(*, days: int = SHADOW_WINDOW_DAYS) -> str:
    until = datetime.now(UTC).replace(microsecond=0) + timedelta(days=days)
    return until.isoformat().replace("+00:00", "Z")


def _header(crystal_id: str, pattern: str, hits: int, source: str) -> str:
    return (
        f'"""Draft crystal — {crystal_id} (L3 safe mode, review before promote).\n'
        f"\n"
        f"Pattern: {pattern}\n"
        f"Hits:    {hits}\n"
        f"Source:  {source}\n"
        f'"""\n'
    )


def _template_body(crystal_id: str, pattern: str) -> str:
    return (
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import argparse\n"
        "import json\n"
        "\n"
        "\n"
        "def main(argv: list[str] | None = None) -> int:\n"
        f"    parser = argparse.ArgumentParser(description={crystal_id!r})\n"
        '    parser.add_argument("--json", action="store_true", help="JSON output")\n'
        "    args = parser.parse_args(argv)\n"
        f"    # TODO: crystallize the repeated task {pattern!r} into deterministic steps.\n"
        f'    payload = {{"ok": False, "todo": "implement draft crystal", "crystal_id": {crystal_id!r}}}\n'
        "    print(json.dumps(payload) if args.json else payload)\n"
        "    return 0\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def extract_python_code(text: str) -> str | None:
    """Code from an LLM reply: fenced block or raw; must compile."""
    match = _FENCE.search(text)
    code = (match.group(1) if match else text).strip()
    if not code:
        return None
    try:
        compile(code, "<draft>", "exec")
    except SyntaxError:
        return None
    return code + "\n"


def generate_draft_code(
    crystal_id: str,
    pattern: str,
    hits: int,
    *,
    root: Path | None = None,
    log: bool = True,
) -> tuple[str, str]:
    """Draft script text + source ("cheap_llm" | "template").

    The LLM call goes through ``invoke_profile`` so the spend guard applies —
    a metered denial falls back to the template, never to an unguarded call.
    """
    from greedy_token.cheap_llm import cheap_llm_available
    from greedy_token.llm_invoke import invoke_profile
    from greedy_token.settings import get_cheap_llm_settings

    settings = get_cheap_llm_settings(root)
    if cheap_llm_available(settings):
        user = (
            f"Repeated LLM task pattern: {pattern!r} (seen {hits} times).\n"
            f"Script id: {crystal_id}.\n"
            "Write the deterministic Python script that replaces it."
        )
        try:
            result = invoke_profile(
                "crystallize",
                system=DRAFT_SYSTEM_PROMPT,
                user=user,
                root=root,
                allow_escalate=False,
                log=log,
            )
            text = result.text
        except (OSError, ValueError, KeyError, TimeoutError, RuntimeError):
            text = ""
        code = extract_python_code(text) if text else None
        if code:
            return _header(crystal_id, pattern, hits, "cheap_llm") + "\n" + code, "cheap_llm"
    return (
        _header(crystal_id, pattern, hits, "template") + _template_body(crystal_id, pattern),
        "template",
    )


def _shadow_route(crystal_id: str, pattern: str, shadow_until: str) -> dict:
    rel = (DRAFTS_DIR / f"{crystal_id}.py").as_posix()
    return {
        "id": crystal_id,
        "target": "python",
        "read_only": True,
        "enabled": False,
        "shadow_until": shadow_until,
        "patterns": [pattern],
        "command": f"python {rel}",
        "note": "L3 draft crystal — shadow (log-only) until promoted",
    }


def draft_crystal(
    crystal_id: str,
    *,
    root: Path,
    since: str | None = "30d",
    actor: str = "",
    reason: str = "",
    log: bool = True,
) -> DraftResult:
    """Generate a draft script + register a shadow route. Raises ValueError."""
    err = validate_route_id(crystal_id)
    if err:
        raise ValueError(err)
    if derive_crystal_state(crystal_id)["state"] == STATE_APPLIED:
        raise ValueError(
            f"crystal {crystal_id!r} is already applied — "
            "reject it before re-drafting"
        )
    crystal = find_crystal(crystal_id, since=since)
    if crystal is None:
        raise ValueError(
            f"crystal {crystal_id!r} not found in candidates (since={since}); "
            "run greedy-token hub / crystallize report first"
        )
    pattern = str(crystal.get("pattern") or "")
    err = validate_route_id(crystal_id, pattern=pattern)
    if err:
        raise ValueError(err)
    hits = int(crystal.get("hits") or 0)
    reasons = pattern_violations(pattern)
    if reasons:
        raise ValueError(
            f"pattern for {crystal_id!r} fails scripts lint: {'; '.join(reasons)}"
        )

    code, source = generate_draft_code(crystal_id, pattern, hits, root=root, log=log)
    path = draft_path(root, crystal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    path.chmod(0o755)

    shadow_until = _shadow_until_iso()
    route = _shadow_route(crystal_id, pattern, shadow_until)
    config_path = upsert_workspace_routes(root, {"routes": [route]})

    lint = lint_routes(root=root, routes=[route])

    who = actor or default_actor()
    append_lifecycle_event(
        stage="draft",
        crystal_id=crystal_id,
        pattern=pattern,
        hits=hits,
        status="pending",
        actor=who,
        reason=reason,
        extra={"draft_path": str(path), "source": source},
    )
    append_lifecycle_event(
        stage="shadow",
        crystal_id=crystal_id,
        pattern=pattern,
        hits=hits,
        status="pending",
        actor=who,
        extra={"shadow_until": shadow_until, "route_id": crystal_id},
    )

    return DraftResult(
        crystal_id=crystal_id,
        pattern=pattern,
        hits=hits,
        draft_path=path,
        config_path=config_path,
        shadow_until=shadow_until,
        source=source,
        lint_ok=bool(lint.get("ok")),
        lint_violations=list(lint.get("violations") or []),
    )


def _workspace_route(root: Path, crystal_id: str) -> dict | None:
    for route in workspace_config_routes(root):
        if route.get("id") == crystal_id:
            return route
    return None


def _merged_route(root: Path, crystal_id: str) -> dict | None:
    """Route id resolved against bundled + workspace overlay (routes_file too).

    Crystal drafts are inline workspace routes, but the same id may already
    exist as a regular route living in ``routes_file`` or the bundled config —
    status should report that reality instead of ``route: none``.
    """
    for route in load_routes_config(root).get("routes") or []:
        if isinstance(route, dict) and route.get("id") == crystal_id:
            return route
    return None


def approve_crystal(
    crystal_id: str,
    *,
    root: Path,
    actor: str = "",
    reason: str = "",
) -> dict:
    """proposed → approved: human decision, pinned to the reviewed draft bytes.

    Records who/why plus ``approved_sha256`` and the ``workspace_id`` (the
    same hash that keys ``~/.greedy-token/trust/<id>/``) — promote refuses
    to apply when the draft changed after approval or the approval was
    recorded for another workspace.
    """
    err = validate_route_id(crystal_id)
    if err:
        raise ValueError(err)
    state = str(derive_crystal_state(crystal_id)["state"])
    if state == STATE_APPLIED:
        raise ValueError(f"crystal {crystal_id!r} is already applied")
    if state == STATE_REJECTED:
        raise ValueError(
            f"crystal {crystal_id!r} was rejected — draft again to re-propose"
        )
    if state not in (STATE_PROPOSED, STATE_APPROVED):
        raise ValueError(
            f"crystal {crystal_id!r} has no proposal to approve (state: {state}); "
            "run 'greedy-token crystallize draft' first"
        )
    path = draft_path(root, crystal_id)
    if not path.is_file():
        raise ValueError(
            f"draft script missing: {path}; "
            "run 'greedy-token crystallize draft' first"
        )
    sha = sha256_file(path)
    workspace_id = _workspace_id(root)
    route = _workspace_route(root, crystal_id)
    pattern, hits = _proposal_meta(crystal_id, route)
    event = append_lifecycle_event(
        stage="approved",
        crystal_id=crystal_id,
        pattern=pattern,
        hits=hits,
        status="approved",
        actor=actor or default_actor(),
        reason=reason,
        extra={
            "transition": f"{state}->{STATE_APPROVED}",
            "approved_sha256": sha,
            "workspace_id": workspace_id,
            "draft_path": str(path),
        },
    )
    return {
        "ok": True,
        "crystal_id": crystal_id,
        "state": STATE_APPROVED,
        "approved_sha256": sha,
        "workspace_id": workspace_id,
        "actor": event.get("actor", ""),
        "reason": reason,
    }


def promote_crystal(
    crystal_id: str,
    *,
    root: Path,
    actor: str = "",
    reason: str = "",
) -> dict:
    """approved → applied: trust the pinned draft, then activate the route.

    Apply never grants execution authority by itself — the draft passes
    through Step-2 trust (``approve_script`` binds the reviewed bytes in the
    user-local manifest) before ``shadow_until``/``enabled: false`` are
    dropped. Requires the recorded sha256 pin from ``approve`` (made for
    this workspace) and re-verifies the bound bytes after the trust write,
    so a draft changed between the check and the bind is never trusted.
    """
    info = derive_crystal_state(crystal_id)
    state = str(info["state"])
    if state == STATE_APPLIED:
        raise ValueError(f"crystal {crystal_id!r} is already applied")
    if state == STATE_REJECTED:
        raise ValueError(
            f"crystal {crystal_id!r} was rejected — draft again to re-propose"
        )
    if state in (STATE_UNKNOWN, STATE_CANDIDATE):
        raise ValueError(
            f"crystal {crystal_id!r} has no proposal (state: {state}); "
            "run 'greedy-token crystallize draft' first"
        )
    if state == STATE_PROPOSED:
        raise ValueError(
            f"crystal {crystal_id!r} is proposed but not approved; "
            "run 'greedy-token crystallize approve' first"
        )
    route = _workspace_route(root, crystal_id)
    if route is None:
        raise ValueError(
            f"route {crystal_id!r} not found in {WORKSPACE_CONFIG_NAME}; "
            "draft it first: greedy-token crystallize draft"
        )
    if "shadow_until" not in route:
        raise ValueError(f"route {crystal_id!r} is not in shadow — nothing to promote")
    script_rel = extract_script_path(str(route.get("command") or "")) or (
        DRAFTS_DIR / f"{crystal_id}.py"
    ).as_posix()
    script = root / script_rel
    if not script.is_file():
        raise ValueError(f"draft script missing: {script}")
    approved = info.get("approved") or {}
    pinned = str(approved.get("approved_sha256") or "")
    if not pinned:
        raise ValueError(
            f"approval for {crystal_id!r} carries no approved_sha256 pin; "
            "run 'greedy-token crystallize approve' to pin the reviewed bytes"
        )
    if str(approved.get("workspace_id") or "") != _workspace_id(root):
        raise ValueError(
            f"approval for {crystal_id!r} was recorded for a different workspace; "
            "run 'greedy-token crystallize approve' in this workspace"
        )
    sha = sha256_file(script)
    if pinned != sha:
        raise ValueError(
            f"draft for {crystal_id!r} changed since approval "
            f"(approved {pinned[:12]}…, current {sha[:12]}…); "
            "review the new bytes and run 'crystallize approve' again"
        )
    entry = approve_script(
        root,
        script_rel,
        approval_source=_APPROVAL_SOURCE_PROMOTE,
        note=(
            f"crystal {crystal_id}; approved {approved.get('ts', '')}"
            f" by {approved.get('actor', '')}"
        ).strip(),
    )
    if entry.sha256 != pinned:
        # The draft raced the pin check → trust write; never leave bytes the
        # reviewer did not approve bound in the manifest.
        revoke_script(root, entry.path)
        raise ValueError(
            f"draft for {crystal_id!r} changed while it was being applied "
            f"(approved {pinned[:12]}…, bound {entry.sha256[:12]}…); "
            "review the new bytes and run 'crystallize approve' again"
        )
    route.pop("shadow_until", None)
    route.pop("enabled", None)
    route["note"] = "L3 crystal — promoted after human review"
    config_path = upsert_workspace_routes(root, {"routes": [route]})
    pattern = str((route.get("patterns") or [""])[0])
    append_lifecycle_event(
        stage="promoted",
        crystal_id=crystal_id,
        pattern=pattern,
        status="active",
        actor=actor or default_actor(),
        reason=reason,
        extra={
            "transition": f"{STATE_APPROVED}->{STATE_APPLIED}",
            "route_id": crystal_id,
            "script_path": entry.path,
            "trusted": f"manifest:{entry.path}",
            "sha256": entry.sha256,
        },
    )
    return {
        "ok": True,
        "crystal_id": crystal_id,
        "state": STATE_APPLIED,
        "config": str(config_path),
        "route": route,
        "trusted": f"manifest:{entry.path}",
        "sha256": entry.sha256,
    }


def _candidate_trust_paths(
    crystal_id: str, route: dict | None
) -> set[str]:
    """Every workspace-relative script path this crystal may have bound in trust:
    the canonical draft, the script its route command points at, and every path
    recorded by past promotes (``script_path`` / ``trusted: manifest:<path>``).
    """
    paths = {(DRAFTS_DIR / f"{crystal_id}.py").as_posix()}
    if route:
        rel = extract_script_path(str(route.get("command") or ""))
        if rel:
            paths.add(rel)
    for event in load_lifecycle_events():
        if event.get("crystal_id") != crystal_id:
            continue
        script_path = event.get("script_path")
        if isinstance(script_path, str) and script_path:
            paths.add(script_path)
        trusted = str(event.get("trusted") or "")
        if trusted.startswith("manifest:"):
            paths.add(trusted.removeprefix("manifest:"))
    return paths


def reject_crystal(
    crystal_id: str,
    *,
    root: Path,
    actor: str = "",
    reason: str = "",
) -> dict:
    """Any state → rejected: drop the draft, its route, and its trust entries."""
    if (
        not crystal_id.strip()
        or "/" in crystal_id
        or "\\" in crystal_id
        or "\x00" in crystal_id
        or crystal_id in (".", "..")
    ):
        raise ValueError(f"invalid crystal id for reject: {crystal_id!r}")
    route = _workspace_route(root, crystal_id)
    pattern = str((route.get("patterns") or [""])[0]) if route else ""
    previous = str(derive_crystal_state(crystal_id)["state"])
    removed_route = remove_workspace_route(root, crystal_id)
    path = draft_path(root, crystal_id)
    removed_draft = path.is_file()
    if removed_draft:
        path.unlink()
    revoked: list[str] = []
    for candidate in sorted(_candidate_trust_paths(crystal_id, route)):
        try:
            if revoke_script(root, candidate):
                revoked.append(candidate)
        except TrustError:
            continue  # lifecycle junk (e.g. "../x.py") must not brick cleanup
    append_lifecycle_event(
        stage="rejected",
        crystal_id=crystal_id,
        pattern=pattern,
        status="rejected",
        actor=actor or default_actor(),
        reason=reason,
        extra={
            "transition": f"{previous}->{STATE_REJECTED}",
            "removed_route": removed_route,
            "removed_draft": removed_draft,
            "revoked_trust": bool(revoked),
            "revoked_paths": revoked,
        },
    )
    return {
        "ok": True,
        "crystal_id": crystal_id,
        "removed_route": removed_route,
        "removed_draft": removed_draft,
        "revoked_trust": bool(revoked),
        "revoked_paths": revoked,
    }


_NEXT_HINT = {
    STATE_CANDIDATE: "greedy-token crystallize draft {id}",
    STATE_PROPOSED: "greedy-token crystallize approve {id} [--reason …]",
    STATE_APPROVED: "greedy-token crystallize promote {id}",
    STATE_APPLIED: "live — inspect via 'greedy-token capabilities show {id}'",
    STATE_REJECTED: "terminal — 'greedy-token crystallize draft {id}' re-proposes",
    STATE_UNKNOWN: "not a known crystal — see 'greedy-token crystallize candidates'",
}


def crystal_status(crystal_id: str, *, root: Path) -> dict:
    """Derived lifecycle state + filesystem/route/trust facts + timeline."""
    info = derive_crystal_state(crystal_id)
    state = str(info["state"])
    candidate = None
    if state == STATE_UNKNOWN:
        candidate = find_crystal(crystal_id, since=None)
        if candidate is not None:
            state = STATE_CANDIDATE
    path = draft_path(root, crystal_id)
    draft_exists = path.is_file()
    route = _workspace_route(root, crystal_id)
    if route is None:
        route = _merged_route(root, crystal_id)
    rel = extract_script_path(str((route or {}).get("command") or "")) or (
        DRAFTS_DIR / f"{crystal_id}.py"
    ).as_posix()
    trusted = rel in trusted_manifest_paths(root)
    trust_check = ""
    if trusted:
        try:
            checks = verify_trust_manifest(root)
            found = next((c for c in checks if c.entry.path == rel), None)
            trust_check = "ok" if (found and found.ok) else (found.code if found else "")
        except TrustError:
            trust_check = "manifest_error"
    events = [
        e for e in load_lifecycle_events() if e.get("crystal_id") == crystal_id
    ]
    events.sort(key=lambda e: str(e.get("ts") or ""))
    approved = info.get("approved") or {}
    if route:
        pattern = str((route.get("patterns") or [""])[0])
    else:
        pattern = str((candidate or {}).get("pattern") or "")
    result: dict = {
        "ok": True,
        "crystal_id": crystal_id,
        "state": state,
        "pattern": pattern,
        "hits": int((candidate or {}).get("hits") or 0),
        "draft": {
            "path": str(path),
            "exists": draft_exists,
            "sha256": sha256_file(path) if draft_exists else "",
        },
        "route": {
            "present": route is not None,
            "status": (
                "shadow"
                if route and route.get("shadow_until")
                else ("active" if route else "none")
            ),
            "shadow_until": str((route or {}).get("shadow_until") or ""),
        },
        "trust": {"approved": trusted, "check": trust_check},
        "approved": {
            "actor": approved.get("actor", ""),
            "reason": approved.get("reason", ""),
            "ts": approved.get("ts", ""),
            "approved_sha256": approved.get("approved_sha256", ""),
            "workspace_id": approved.get("workspace_id", ""),
            "workspace_match": bool(approved.get("workspace_id"))
            and approved["workspace_id"] == _workspace_id(root),
        },
        "timeline": [
            {
                "stage": e.get("stage"),
                "ts": e.get("ts"),
                "actor": e.get("actor", ""),
                "status": e.get("status", ""),
            }
            for e in events
        ],
        "next": _NEXT_HINT.get(state, _NEXT_HINT[STATE_UNKNOWN]).format(id=crystal_id),
    }
    return result
