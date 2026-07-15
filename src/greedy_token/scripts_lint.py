"""Lint python-tier routes: crystallize pattern blocklist + script exists."""

from __future__ import annotations

import re
from pathlib import Path

from greedy_token.paths import load_routes_config

GENERIC_VERBS = frozenset(
    {
        "fix",
        "update",
        "check",
        "audit",
        "refactor",
        "wire",
        "add",
        "make",
    }
)

CREATIVE_STEMS = (
    "refactor",
    "wire",
    "redesign",
    "architecture",
    "implement feature",
)

_COMMAND_SCRIPT = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:python(?:3)?\s+|./)?(?P<path>(?:scripts|stacks|projects)/[^\s;&|]+)",
    re.IGNORECASE,
)


def extract_script_path(command: str | None) -> str | None:
    if not command:
        return None
    cmd = command.strip()
    # Prefer explicit python / relative script forms.
    m = re.match(r"python(?:3)?\s+(\S+)", cmd)
    if m:
        return m.group(1)
    m = re.match(r"\./(\S+)", cmd)
    if m:
        return m.group(1)
    m = _COMMAND_SCRIPT.search(cmd)
    if m:
        return m.group("path")
    # Bare path starting with scripts/ etc.
    if cmd.startswith(("scripts/", "stacks/", "projects/")):
        return cmd.split()[0]
    return None


def _is_consumer_script(route: dict) -> bool:
    if route.get("external") is True or route.get("consumer") is True:
        return True
    note = str(route.get("note") or "").lower()
    return "consumer repo" in note


def _is_package_only_root(root: Path) -> bool:
    """True when linting the published package tree (no monorepo docs/)."""
    return (root / "src" / "greedy_token").is_dir() and not (
        root / "docs" / "phase-manifest.json"
    ).is_file()


def pattern_violations(pattern: str) -> list[str]:
    p = (pattern or "").strip().lower()
    if not p:
        return ["empty pattern"]
    reasons: list[str] = []
    tokens = p.split()
    if len(tokens) == 1 and tokens[0] in GENERIC_VERBS:
        reasons.append(f'lone generic verb "{p}"')
    for stem in CREATIVE_STEMS:
        if stem == "wire":
            if re.search(r"\bwire(?:d|ing)?\b", p):
                reasons.append(f'forbidden creative/wiring stem "wire" in "{p}"')
                break
            continue
        if stem in p:
            reasons.append(f'forbidden creative/wiring stem "{stem}" in "{p}"')
            break
    return reasons


def lint_routes(
    *,
    root: Path,
    routes: list[dict] | None = None,
) -> dict:
    """Return {ok, violations[], checked_routes, checked_patterns}."""
    if routes is None:
        cfg = load_routes_config()
        routes = list(cfg.get("routes") or [])

    violations: list[dict] = []
    checked_routes = 0
    checked_patterns = 0

    for route in routes:
        if (route.get("target") or "").lower() != "python":
            continue
        checked_routes += 1
        rid = route.get("id") or "unknown"
        command = route.get("command")
        script = extract_script_path(command)
        if not _is_consumer_script(route):
            if not script:
                violations.append(
                    {
                        "id": rid,
                        "kind": "script_missing",
                        "detail": f'cannot extract script path from command "{command}"',
                    }
                )
            else:
                full = (root / script).resolve()
                try:
                    full.relative_to(root.resolve())
                except ValueError:
                    full = root / script
                if not full.is_file() and not _is_package_only_root(root):
                    # Package CI: routes point at monorepo scripts that are not shipped.
                    violations.append(
                        {
                            "id": rid,
                            "kind": "script_missing",
                            "detail": f'script not found "{script}"',
                        }
                    )

        for pat in route.get("patterns") or []:
            checked_patterns += 1
            for reason in pattern_violations(str(pat)):
                violations.append(
                    {
                        "id": rid,
                        "kind": "forbidden_pattern",
                        "detail": f'forbidden pattern "{pat}": {reason}',
                    }
                )

    return {
        "ok": not violations,
        "violations": violations,
        "checked_routes": checked_routes,
        "checked_patterns": checked_patterns,
    }


def format_lint_report(result: dict) -> str:
    if result.get("ok"):
        return (
            f"scripts lint OK — {result.get('checked_routes', 0)} python routes, "
            f"{result.get('checked_patterns', 0)} patterns"
        )
    lines = ["scripts lint FAILED:"]
    for v in result.get("violations") or []:
        lines.append(f"  {v['id']}: {v['detail']}")
    return "\n".join(lines)
