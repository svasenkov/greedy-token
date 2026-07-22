from __future__ import annotations

import json
import os
from pathlib import Path

import allure
import pytest
from allure_commons._allure import fixture as allure_fixture_wrapper

from tests.ollama_stub import clear_ollama_probe_cache, install_ollama_scripts, ollama_stub_server
from tests.pyramid_layers import layer_for_module
from tests.testops_ids import TESTOPS_IDS

from datetime import datetime, timezone

from greedy_token.usage import append_event


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--release-version",
        action="store",
        default=None,
        help="Target release semver for @pytest.mark.release gate tests",
    )


@pytest.fixture
def release_version(request: pytest.FixtureRequest) -> str | None:
    cli = request.config.getoption("--release-version")
    if cli:
        return str(cli).strip()
    env = os.environ.get("GREEDY_TOKEN_RELEASE_VERSION", "").strip()
    return env or None


def _discover_workspace_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "phase-manifest.json").is_file() and (
            parent / "stacks" / "java-spring"
        ).is_dir():
            return parent
    return None


def _humanize_fixture_teardown(fixture_name: str, finalizer_name: str | int) -> str:
    suffix = str(finalizer_name)
    if suffix in ("<lambda>", "1") or suffix.isdigit():
        return f"Cleanup {fixture_name}"
    return f"{fixture_name}::{suffix}"


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_fixture_setup(fixturedef, request):
    """Rename pytest builtin finalizers (<lambda>, ::1) for readable Allure teardown."""
    yield
    fixture_name = getattr(fixturedef.func, "__allure_display_name__", fixturedef.argname)
    for finalizer in getattr(fixturedef, "_finalizers", []):
        if isinstance(finalizer, allure_fixture_wrapper):
            raw = finalizer._name.split("::", 1)[-1]
            finalizer._name = _humanize_fixture_teardown(fixture_name, raw)


@allure.title("Workspace workspace")
@pytest.fixture
def workspace_root(monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _discover_workspace_root()
    if root is None:
        pytest.skip("workspace root not found (parent workspace checkout required)")
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(root))
    return root


# Workspace routes overlay — merged over bundled routes.yaml via .greedy-token.yaml.
WORKSPACE_ROUTES_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "routes"
    / "workspace-routes.yaml"
)


@allure.title("Minimal workspace")
@pytest.fixture
def minimal_workspace(tmp_path: Path) -> Path:
    (tmp_path / "workspace-routes.yaml").write_text(
        WORKSPACE_ROUTES_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / ".greedy-token.yaml").write_text(
        "routes_file: workspace-routes.yaml\n", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    meta_sync = tmp_path / "scripts" / "meta-sync-check.py"
    meta_sync.write_text("#!/usr/bin/env python\nprint('meta-sync-check-ok')\n", encoding="utf-8")
    meta_sync.chmod(0o755)
    bool_audit = tmp_path / "scripts" / "configurator-boolean-audit.py"
    bool_audit.write_text('#!/usr/bin/env python\nprint(\'{"ok": true}\')\n', encoding="utf-8")
    bool_audit.chmod(0o755)
    (tmp_path / "stacks").mkdir()
    (tmp_path / "generators").mkdir()
    ollama_scripts = tmp_path / "scripts" / "ollama"
    ollama_scripts.mkdir(parents=True)
    (ollama_scripts / "audit-skill.sh").write_text("#!/bin/sh\necho audit\n", encoding="utf-8")
    (ollama_scripts / "classify-file.sh").write_text("#!/bin/sh\necho classify\n", encoding="utf-8")

    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "test.mdc").write_text("always-on rule for token audit", encoding="utf-8")

    rag = tmp_path / "docs" / "rag"
    rag.mkdir()
    chunk_rel = "docs/rag/config/test-chunk.md"
    (rag / "config").mkdir(parents=True)
    (rag / "config" / "test-chunk.md").write_text(
        "baseUrl is configured via -DbaseUrl flag in Gradle.\n",
        encoding="utf-8",
    )
    manifest_line = {
        "id": "test-baseurl",
        "domain": "config",
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


def _seed_crystal_candidate(log: Path, task: str, hits: int = 5) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    for _ in range(hits):
        append_event(
            {"ts": ts, "selected_tier": "cursor", "task": task, "route_id": "cursor-fallback"},
            path=log,
        )


@allure.title("Crystallize candidate home")
@pytest.fixture
def crystal_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated GREEDY_TOKEN_HOME + usage log with one seeded LLM-tier candidate."""
    home = tmp_path / "gt-home"
    home.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    log = home / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    _seed_crystal_candidate(log, "summarize weekly spend report table")
    return home


@allure.title("Disable cheap LLM for deterministic crystallize drafts")
@pytest.fixture
def no_cheap_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "greedy_token.cheap_llm.cheap_llm_available",
        lambda settings, timeout=2.0: False,
    )


@allure.title("Ollama stub server")
@pytest.fixture
def ollama_stub(monkeypatch: pytest.MonkeyPatch) -> str:
    clear_ollama_probe_cache()
    with ollama_stub_server() as url:
        monkeypatch.setenv("OLLAMA_URL", url)
        monkeypatch.setenv("OLLAMA_MODEL", "stub-model")
        clear_ollama_probe_cache()
        yield url
    clear_ollama_probe_cache()


@allure.title("Workspace with Ollama scripts")
@pytest.fixture
def ollama_workspace(minimal_workspace: Path, ollama_stub: str) -> Path:
    install_ollama_scripts(minimal_workspace)
    return minimal_workspace


@allure.title("Clear cheap LLM env")
@pytest.fixture(autouse=True)
def _clear_cheap_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OLLAMA_URL",
        "OLLAMA_MODEL",
        "CHEAP_LLM_PROVIDER",
        "CHEAP_LLM_URL",
        "CHEAP_LLM_MODEL",
        "CHEAP_LLM_API_KEY",
        "GREEDY_LLM_MODEL_ID",
        "GREEDY_LLM_PROFILE",
        "GREEDY_LLM_TIER",
        "GREEDY_EXPENSIVE_LLM",
        "GREEDY_ALLOW_EXPENSIVE",
        "GREEDY_TOKEN_FOOTER_STYLE",
    ):
        monkeypatch.delenv(key, raising=False)


@allure.title("Isolate user config from developer HOME")
@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ~/.greedy-token/config.yaml on the dev machine from affecting unit tests."""
    missing = tmp_path / "no-user-greedy-token-config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: missing)
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: missing)


@allure.title("Set GREEDY_TOKEN_ROOT")
@pytest.fixture(autouse=True)
def _greedy_token_root_env(monkeypatch: pytest.MonkeyPatch, minimal_workspace: Path) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))


@allure.title("Isolate usage log from the developer HOME")
@pytest.fixture(autouse=True)
def _isolate_usage_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the telemetry log to a per-test temp file so the suite never reads
    from or writes to the real ~/.greedy-token/usage.jsonl. Tests that need a
    specific log path still override GREEDY_TOKEN_LOG themselves."""
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark tests with pyramid layer for pytest -m and CI matrix slices."""
    for item in items:
        module_name = item.module.__name__.rsplit(".", 1)[-1]
        layer = layer_for_module(module_name)
        if layer:
            item.add_marker(getattr(pytest.mark, layer))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Attach Allure id + label layer=* for TestOps (5276)."""
    try:
        import allure
    except ImportError:
        return
    testops_id = TESTOPS_IDS.get(item.nodeid)
    if testops_id:
        allure.dynamic.id(testops_id)
    module_name = item.module.__name__.rsplit(".", 1)[-1]
    layer = layer_for_module(module_name)
    if layer:
        allure.dynamic.label("layer", layer)
