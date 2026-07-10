"""Package version resolution — SSOT pyproject.toml; CLI override for release gate."""

from __future__ import annotations

import os
from pathlib import Path

RELEASE_VERSION_ENV = "GREEDY_TOKEN_RELEASE_VERSION"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_pyproject_version(root: Path | None = None) -> str:
    pyproject = (root or repo_root()) / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("version = "))
    return line.split("=", 1)[1].strip().strip('"')


def metadata_version() -> str:
    from importlib.metadata import version

    return version("greedy-token")


def resolve_version() -> str:
    root = repo_root()
    if (root / "pyproject.toml").is_file():
        return read_pyproject_version(root)
    try:
        return metadata_version()
    except Exception:
        override = os.environ.get(RELEASE_VERSION_ENV, "").strip()
        if override:
            return override
        raise RuntimeError(
            f"greedy-token version unavailable: install package or set {RELEASE_VERSION_ENV}"
        ) from None
