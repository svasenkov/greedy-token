from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from greedy_token.paths import find_workspace_root


def provider_jsonl_paths(root: Path) -> tuple[Path, Path]:
    raw = root / "projects" / "infra-home" / "raw" / "providers"
    return raw / "provider-catalog.jsonl", raw / "local-models-reference.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def max_verified_at(rows: list[dict[str, Any]]) -> str | None:
    dates = [str(row.get("verified_at")) for row in rows if row.get("verified_at")]
    return max(dates) if dates else None


def file_meta(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "count": len(rows),
        "mtime": stat.st_mtime,
        "mtime_iso": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "as_of": max_verified_at(rows),
    }


def catalog_payload(*, root: Path | None = None) -> tuple[int, dict]:
    try:
        workspace = root or find_workspace_root()
    except SystemExit:
        return 404, {
            "error": "workspace root not found",
            "hint": "Set GREEDY_TOKEN_ROOT to the monorepo root",
        }

    catalog_path, _ = provider_jsonl_paths(workspace)
    if not catalog_path.is_file():
        return 404, {
            "error": "provider-catalog.jsonl not found",
            "path": str(catalog_path),
        }

    items = load_jsonl(catalog_path)
    payload = {
        "items": items,
        "count": len(items),
        "path": str(catalog_path),
        "home_region": "RU",
    }
    payload.update(file_meta(catalog_path, items))
    return 200, payload


def local_models_payload(*, root: Path | None = None) -> tuple[int, dict]:
    try:
        workspace = root or find_workspace_root()
    except SystemExit:
        return 404, {
            "error": "workspace root not found",
            "hint": "Set GREEDY_TOKEN_ROOT to the monorepo root",
        }

    _, models_path = provider_jsonl_paths(workspace)
    if not models_path.is_file():
        return 404, {
            "error": "local-models-reference.jsonl not found",
            "path": str(models_path),
        }

    items = load_jsonl(models_path)
    payload = {
        "items": items,
        "count": len(items),
        "path": str(models_path),
        "sync_command": "python scripts/infra/sync_local_models.py --dry-run",
    }
    payload.update(file_meta(models_path, items))
    return 200, payload
