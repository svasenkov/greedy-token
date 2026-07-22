from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_CONFIG_NAME = ".greedy-token.yaml"


def find_workspace_root(start: Path | None = None) -> Path:
    env = os.environ.get("GREEDY_TOKEN_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if root.is_dir():
            return root
        raise SystemExit(f"GREEDY_TOKEN_ROOT is not a directory: {root}")

    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / "docs" / "phase-manifest.json").is_file() and (
            parent / "scripts" / "meta-sync-check.py"
        ).is_file():
            return parent

    raise SystemExit(
        "Cannot find workspace root. Set GREEDY_TOKEN_ROOT=/path/to/workspace"
    )


def _read_yaml_dict(path: Path) -> dict:
    import yaml

    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _routes_list(cfg: dict) -> list[dict]:
    routes = cfg.get("routes")
    if not isinstance(routes, list):
        return []
    return [r for r in routes if isinstance(r, dict) and r.get("id")]


def bundled_routes_config() -> dict:
    """Generic default routes shipped with the package."""
    import yaml

    config_path = Path(__file__).parent / "config" / "routes.yaml"
    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def workspace_routes_overlay(root: Path) -> dict:
    """Workspace route overlay from <root>/.greedy-token.yaml.

    Keys: ``routes`` (inline list), ``routes_file`` (path to a YAML with
    ``routes:``/``cursor_fallback:``, relative to root or absolute), and
    ``cursor_fallback``. Inline entries win over ``routes_file`` on same id.
    """
    cfg = _read_yaml_dict(root / WORKSPACE_CONFIG_NAME)
    routes: list[dict] = []
    cursor_fallback: dict = {}

    routes_file = str(cfg.get("routes_file") or "").strip()
    if routes_file:
        file_path = Path(routes_file).expanduser()
        if not file_path.is_absolute():
            file_path = root / file_path
        file_cfg = _read_yaml_dict(file_path)
        routes.extend(_routes_list(file_cfg))
        if isinstance(file_cfg.get("cursor_fallback"), dict):
            cursor_fallback = file_cfg["cursor_fallback"]

    inline = _routes_list(cfg)
    if inline:
        inline_ids = {r["id"] for r in inline}
        routes = [r for r in routes if r["id"] not in inline_ids] + inline
    if isinstance(cfg.get("cursor_fallback"), dict):
        cursor_fallback = cfg["cursor_fallback"]

    overlay: dict = {}
    if routes:
        overlay["routes"] = routes
    if cursor_fallback:
        overlay["cursor_fallback"] = cursor_fallback
    return overlay


def merge_routes_config(base: dict, overlay: dict) -> dict:
    """Overlay wins: same id replaces the bundled route; new ids are prepended
    so they also win tier tie-breaks against bundled routes."""
    if not overlay:
        return base
    overlay_routes = _routes_list(overlay)
    overlay_ids = {r["id"] for r in overlay_routes}
    merged = dict(base)
    merged["routes"] = overlay_routes + [
        r for r in _routes_list(base) if r["id"] not in overlay_ids
    ]
    if isinstance(overlay.get("cursor_fallback"), dict):
        merged["cursor_fallback"] = overlay["cursor_fallback"]
    return merged


SCAFFOLD_SKIP_DIRS = {"node_modules", "build", "dist", "__pycache__", ".venv", ".tox"}


def detect_search_paths(root: Path) -> list[str]:
    """Top-level project folders for a tool-rg-search scaffold (hidden/vendor skipped)."""
    dirs = sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in SCAFFOLD_SKIP_DIRS
    )
    return dirs or ["."]


def scaffold_routes_overlay(root: Path) -> dict:
    """Bundled tool-rg-search route with search_paths detected from the project tree."""
    base = next(
        r for r in bundled_routes_config()["routes"] if r["id"] == "tool-rg-search"
    )
    route = dict(base)
    route["search_paths"] = detect_search_paths(root)
    return {"routes": [route]}


def upsert_workspace_routes(root: Path, new_cfg: dict) -> Path:
    """Merge routes (and cursor_fallback) from new_cfg into <root>/.greedy-token.yaml.

    Existing routes with the same id are replaced in place; new ids are appended.
    Returns the workspace config path.
    """
    import yaml

    path = root / WORKSPACE_CONFIG_NAME
    cfg = _read_yaml_dict(path)
    incoming = _routes_list(new_cfg)
    incoming_by_id = {r["id"]: r for r in incoming}
    merged = [incoming_by_id.pop(r["id"], r) for r in _routes_list(cfg)]
    merged.extend(r for r in incoming if r["id"] in incoming_by_id)
    cfg["routes"] = merged
    if isinstance(new_cfg.get("cursor_fallback"), dict):
        cfg["cursor_fallback"] = new_cfg["cursor_fallback"]
    path.write_text(
        yaml.safe_dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def workspace_config_routes(root: Path) -> list[dict]:
    """Inline routes currently stored in <root>/.greedy-token.yaml (no routes_file)."""
    return _routes_list(_read_yaml_dict(root / WORKSPACE_CONFIG_NAME))


def remove_workspace_route(root: Path, route_id: str) -> bool:
    """Drop an inline route by id from <root>/.greedy-token.yaml. True if removed."""
    import yaml

    path = root / WORKSPACE_CONFIG_NAME
    cfg = _read_yaml_dict(path)
    routes = _routes_list(cfg)
    kept = [r for r in routes if r.get("id") != route_id]
    if len(kept) == len(routes):
        return False
    cfg["routes"] = kept
    path.write_text(
        yaml.safe_dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return True


def load_routes_config(root: Path | None = None) -> dict:
    """Bundled generic routes merged with the workspace overlay (if any).

    Without an explicit ``root`` the workspace is resolved via
    ``GREEDY_TOKEN_ROOT`` / auto-discovery; outside a workspace the bundled
    defaults are returned as-is.
    """
    base = bundled_routes_config()
    if root is None:
        try:
            root = find_workspace_root()
        except SystemExit:
            return base
    return merge_routes_config(base, workspace_routes_overlay(root))
