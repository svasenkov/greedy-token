from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


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
    if rel.endswith(".py"):
        base = f"cd {root} && python {rel}"
    else:
        base = f"cd {root} && ./{rel}"
    return f"{base} {extra_args}".strip()


def ollama_available(url: str | None = None, timeout: float = 2.0) -> bool:
    base = url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.load(resp)
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False


def ollama_status_line() -> str:
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:14b")
    if ollama_available(url):
        return f"Ollama: available ({url}, model={model})"
    return f"Ollama: unavailable ({url}) — ollama routes need local server or skip to cursor"
