"""Crystallization L3 in safe mode: draft → shadow route → promote / reject.

No silent auto-apply. ``crystallize draft`` generates a reviewable Python
script in ``.greedy-token/drafts/`` (cheap LLM when available, deterministic
template otherwise) and registers a *shadow* route in the workspace config
(``shadow_until`` +7d, ``enabled: false``). A shadow route never changes
``route_task`` — it is log-only. Only a human ``promote`` activates the route;
``reject`` removes the draft and the route. Every transition writes a
lifecycle event (draft → shadow → promoted / rejected) that the hub shows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from greedy_token.hub.crystallize import append_lifecycle_event, list_crystals
from greedy_token.paths import (
    WORKSPACE_CONFIG_NAME,
    remove_workspace_route,
    upsert_workspace_routes,
    workspace_config_routes,
)
from greedy_token.scripts_lint import lint_routes, pattern_violations

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
    """Candidate metadata (pattern/hits) from report + inbox + lifecycle."""
    listing = list_crystals(since=since)
    for crystal in listing.get("crystals", []):
        if crystal.get("crystal_id") == crystal_id:
            return crystal
    return None


def _shadow_until_iso(*, days: int = SHADOW_WINDOW_DAYS) -> str:
    until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=days)
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
) -> tuple[str, str]:
    """Draft script text + source ("cheap_llm" | "template")."""
    from greedy_token.cheap_llm import cheap_llm_available, cheap_llm_chat
    from greedy_token.settings import get_cheap_llm_settings

    settings = get_cheap_llm_settings(root)
    if cheap_llm_available(settings):
        user = (
            f"Repeated LLM task pattern: {pattern!r} (seen {hits} times).\n"
            f"Script id: {crystal_id}.\n"
            "Write the deterministic Python script that replaces it."
        )
        try:
            text, _tokens = cheap_llm_chat(settings, system=DRAFT_SYSTEM_PROMPT, user=user)
        except (OSError, ValueError, KeyError, TimeoutError):
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
) -> DraftResult:
    """Generate a draft script + register a shadow route. Raises ValueError."""
    crystal = find_crystal(crystal_id, since=since)
    if crystal is None:
        raise ValueError(
            f"crystal {crystal_id!r} not found in candidates (since={since}); "
            "run greedy-token hub / crystallize report first"
        )
    pattern = str(crystal.get("pattern") or "")
    hits = int(crystal.get("hits") or 0)
    reasons = pattern_violations(pattern)
    if reasons:
        raise ValueError(
            f"pattern for {crystal_id!r} fails scripts lint: {'; '.join(reasons)}"
        )

    code, source = generate_draft_code(crystal_id, pattern, hits, root=root)
    path = draft_path(root, crystal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    path.chmod(0o755)

    shadow_until = _shadow_until_iso()
    route = _shadow_route(crystal_id, pattern, shadow_until)
    config_path = upsert_workspace_routes(root, {"routes": [route]})

    lint = lint_routes(root=root, routes=[route])

    append_lifecycle_event(
        stage="draft",
        crystal_id=crystal_id,
        pattern=pattern,
        hits=hits,
        status="pending",
        extra={"draft_path": str(path), "source": source},
    )
    append_lifecycle_event(
        stage="shadow",
        crystal_id=crystal_id,
        pattern=pattern,
        hits=hits,
        status="pending",
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


def promote_crystal(crystal_id: str, *, root: Path) -> dict:
    """Shadow → active: drop shadow_until, enable the route. Raises ValueError."""
    route = _workspace_route(root, crystal_id)
    if route is None:
        raise ValueError(
            f"route {crystal_id!r} not found in {WORKSPACE_CONFIG_NAME}; "
            "draft it first: greedy-token crystallize draft"
        )
    if "shadow_until" not in route:
        raise ValueError(f"route {crystal_id!r} is not in shadow — nothing to promote")
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
        extra={"route_id": crystal_id},
    )
    return {
        "ok": True,
        "crystal_id": crystal_id,
        "config": str(config_path),
        "route": route,
    }


def reject_crystal(crystal_id: str, *, root: Path) -> dict:
    """Remove the draft script and its route; log the rejected stage."""
    route = _workspace_route(root, crystal_id)
    pattern = str((route.get("patterns") or [""])[0]) if route else ""
    removed_route = remove_workspace_route(root, crystal_id)
    path = draft_path(root, crystal_id)
    removed_draft = path.is_file()
    if removed_draft:
        path.unlink()
    append_lifecycle_event(
        stage="rejected",
        crystal_id=crystal_id,
        pattern=pattern,
        status="rejected",
        extra={"removed_route": removed_route, "removed_draft": removed_draft},
    )
    return {
        "ok": True,
        "crystal_id": crystal_id,
        "removed_route": removed_route,
        "removed_draft": removed_draft,
    }
