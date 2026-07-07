from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from greedy_token.settings import get_ollama_settings
from greedy_token.tool_paths import root_cd_prefix, shell_args

OLLAMA_PROBE_TTL = 3.0
_ollama_probe_cache: dict[str, tuple[float, bool]] = {}


@dataclass(frozen=True)
class ScriptWrapper:
    id: str
    path: str
    category: str
    read_only: bool
    requires_ollama: bool = False
    note: str = ""


WRAPPERS: dict[str, ScriptWrapper] = {
    "check-meta-sync": ScriptWrapper(
        id="check-meta-sync",
        path="scripts/check-meta-sync.sh",
        category="meta",
        read_only=True,
        note="Meta validation — no LLM",
    ),
    "batch-inventory": ScriptWrapper(
        id="batch-inventory",
        path="scripts/ollama/batch-inventory.sh",
        category="ollama",
        read_only=False,
        requires_ollama=True,
        note="Bulk classify via local LLM",
    ),
    "audit-skill": ScriptWrapper(
        id="audit-skill",
        path="scripts/ollama/audit-skill.sh",
        category="ollama",
        read_only=False,
        requires_ollama=True,
        note="Skill quality audit — Ollama",
    ),
    "classify-file": ScriptWrapper(
        id="classify-file",
        path="scripts/ollama/classify-file.sh",
        category="ollama",
        read_only=False,
        requires_ollama=True,
    ),
    "phase1-rsync": ScriptWrapper(
        id="phase1-rsync",
        path="scripts/migrate/phase1-rsync.sh",
        category="migrate",
        read_only=False,
        note="Mechanical rsync — use DRY=1 for preview",
    ),
    "apply-inventory": ScriptWrapper(
        id="apply-inventory",
        path="scripts/migrate/apply-inventory.sh",
        category="migrate",
        read_only=False,
    ),
    "gen-env-configs": ScriptWrapper(
        id="gen-env-configs",
        path="stacks/java-spring/scripts/gen-env-configs.py",
        category="python",
        read_only=False,
        note="Deterministic config generation",
    ),
}


def wrapper_for_command(command: str | None) -> ScriptWrapper | None:
    if not command:
        return None
    for wrapper in WRAPPERS.values():
        if wrapper.path in command or wrapper.id in command:
            return wrapper
    return None


def resolve_wrapper_command(wrapper_id: str, root: Path, *, extra_args: str = "") -> str:
    wrapper = WRAPPERS.get(wrapper_id)
    if not wrapper:
        raise KeyError(f"Unknown wrapper: {wrapper_id}")
    script = root / wrapper.path
    if not script.is_file():
        raise FileNotFoundError(f"Script not found: {script}")
    rel = wrapper.path
    prefix = root_cd_prefix(root)
    if rel.endswith(".py"):
        base = f"{prefix} python {rel}"
    else:
        base = f"{prefix} ./{rel}"
    args = shell_args(extra_args)
    return f"{base} {args}".strip() if args else base


def ollama_available(url: str | None = None, timeout: float = 2.0) -> bool:
    import urllib.error
    import urllib.request

    base = (url or get_ollama_settings().url).rstrip("/")
    now = time.monotonic()
    cached = _ollama_probe_cache.get(base)
    if cached is not None and now - cached[0] < OLLAMA_PROBE_TTL:
        return cached[1]

    try:
        req = urllib.request.Request(f"{base}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.load(resp)
        ok = True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        ok = False
    _ollama_probe_cache[base] = (now, ok)
    return ok


def ollama_status_line() -> str:
    settings = get_ollama_settings()
    url = settings.url
    model = settings.model
    if ollama_available(url):
        return f"Ollama: available ({url}, model={model})"
    return f"Ollama: unavailable ({url}) — ollama routes need local server or skip to cursor"
