from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pyramid_layers import layer_for_module
from tests.ollama_stub import clear_ollama_probe_cache, install_ollama_scripts, ollama_stub_server


def _discover_monorepo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "phase-manifest.json").is_file() and (
            parent / "stacks" / "java-spring"
        ).is_dir():
            return parent
    return None


@pytest.fixture
def monorepo_root(monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _discover_monorepo_root()
    if root is None:
        pytest.skip("monorepo root not found (parent workspace checkout required)")
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(root))
    return root


@pytest.fixture
def minimal_workspace(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    meta_sync = tmp_path / "scripts" / "check-meta-sync.sh"
    meta_sync.write_text("#!/bin/sh\necho check-meta-sync-ok\n", encoding="utf-8")
    meta_sync.chmod(0o755)
    (tmp_path / "stacks").mkdir()
    (tmp_path / "generators").mkdir()
    ollama_scripts = tmp_path / "scripts" / "ollama"
    ollama_scripts.mkdir(parents=True)
    (ollama_scripts / "audit-skill.sh").write_text("#!/bin/sh\necho audit\n", encoding="utf-8")

    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "test.mdc").write_text("always-on rule for token audit", encoding="utf-8")

    rag = tmp_path / "docs" / "rag"
    rag.mkdir()
    chunk_rel = "docs/rag/e2e/test-chunk.md"
    (rag / "e2e").mkdir(parents=True)
    (rag / "e2e" / "test-chunk.md").write_text(
        "baseUrl is configured via -DbaseUrl flag in Gradle.\n",
        encoding="utf-8",
    )
    manifest_line = {
        "id": "test-baseurl",
        "domain": "e2e",
        "path": chunk_rel,
        "tags": ["baseurl", "gradle"],
    }
    (rag / "manifest.jsonl").write_text(json.dumps(manifest_line) + "\n", encoding="utf-8")

    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "sample.js").write_text("const baseUrl = 'http://localhost';\n", encoding="utf-8")

    skill_dir = tmp_path / ".cursor" / "skills" / "configurator-boolean"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# configurator-boolean\n", encoding="utf-8")

    return tmp_path


@pytest.fixture
def ollama_stub(monkeypatch: pytest.MonkeyPatch) -> str:
    clear_ollama_probe_cache()
    with ollama_stub_server() as url:
        monkeypatch.setenv("OLLAMA_URL", url)
        monkeypatch.setenv("OLLAMA_MODEL", "stub-model")
        clear_ollama_probe_cache()
        yield url
    clear_ollama_probe_cache()


@pytest.fixture
def ollama_workspace(minimal_workspace: Path, ollama_stub: str) -> Path:
    install_ollama_scripts(minimal_workspace)
    return minimal_workspace


@pytest.fixture(autouse=True)
def _greedy_token_root_env(monkeypatch: pytest.MonkeyPatch, minimal_workspace: Path) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Attach Allure label layer=* for TestOps pyramid (same key as Java @Layer)."""
    try:
        import allure
    except ImportError:
        return
    module_name = item.module.__name__.rsplit(".", 1)[-1]
    layer = layer_for_module(module_name)
    if layer:
        allure.dynamic.label("layer", layer)
