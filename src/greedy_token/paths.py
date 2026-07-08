from __future__ import annotations

import os
from pathlib import Path


def find_monorepo_root(start: Path | None = None) -> Path:
    env = os.environ.get("GREEDY_TOKEN_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if root.is_dir():
            return root
        raise SystemExit(f"GREEDY_TOKEN_ROOT is not a directory: {root}")

    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / "docs" / "phase-manifest.json").is_file() and (
            parent / "scripts" / "check-meta-sync.sh"
        ).is_file():
            return parent

    raise SystemExit(
        "Cannot find workspace root. Set GREEDY_TOKEN_ROOT=/path/to/workspace"
    )


def load_routes_config() -> dict:
    import yaml

    config_path = Path(__file__).parent / "config" / "routes.yaml"
    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
