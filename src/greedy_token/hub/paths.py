from __future__ import annotations

import os
from pathlib import Path

from greedy_token.usage import log_path


def greedy_home() -> Path:
    raw = os.environ.get("GREEDY_TOKEN_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return log_path().parent


def inbox_path() -> Path:
    return greedy_home() / "crystallize-inbox.json"


def watch_state_path() -> Path:
    return greedy_home() / "crystallize-watch.json"


def lifecycle_path() -> Path:
    return greedy_home() / "crystallize-lifecycle.jsonl"


def sessions_dir() -> Path:
    return greedy_home() / "statusline-sessions"
