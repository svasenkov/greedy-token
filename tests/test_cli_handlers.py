from __future__ import annotations

import argparse
import io
import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import allure
import pytest

import greedy_token.cli as cli
from greedy_token.router import RouteDecision
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("CLI"),
    allure.parent_suite("CLI"),
    allure.feature("CLI handlers"),
    allure.suite("CLI handlers"),
]


def _ns(**kwargs) -> Namespace:
    defaults = {
        "no_log": True,
        "provider": None,
        "list_presets": False,
        "preset": None,
        "routes_from": None,
        "routes_scaffold": False,
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


@allure.story("Parser")
@allure.title("build_parser exposes all subcommands")
def test_build_parser_subcommands() -> None:
    parser = cli.build_parser()
    names = {a.dest for a in parser._actions if hasattr(a, "choices") and a.choices}
    assert "command" in names or parser.parse_args(["route", "task"]).command == "route"


@allure.story("Route")
@allure.title("cmd_route prints decision and returns zero")
def test_cmd_route(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_route(_ns(task="find baseUrl in sample.js"))
    out = capsys.readouterr().out
    attach_text("stdout", out)
    assert code == 0
    assert "Route:" in out or "TOOL" in out


@allure.story("Estimate")
@allure.title("cmd_estimate prints tier scan")
def test_cmd_estimate(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_estimate(_ns(task="find baseUrl in sample.js"))
    out = capsys.readouterr().out
    assert code == 0
    assert "Tier scan:" in out
    assert "← selected" in out


@allure.story("Scripts")
@allure.title("cmd_scripts --execute allows stdout-only audit-skill")
def test_cmd_scripts_run_audit_skill_execute(minimal_workspace: Path) -> None:
    script = minimal_workspace / "scripts" / "ollama" / "audit-skill.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    with patch(
        "subprocess.run",
        return_value=MagicMock(spec=__import__("subprocess").CompletedProcess, returncode=0),
    ) as mock_run:
        code = cli.cmd_scripts(
            _ns(list=False, run="audit-skill", args="configurator-boolean", execute=True)
        )
    assert code == 0
    mock_run.assert_called_once()


@allure.story("Run")
@allure.title("cmd_run dry-run prints plan")
def test_cmd_run_dry_run(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_run(_ns(task="find baseUrl in sample.js", execute=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "Route:" in out


@allure.story("Run")
@allure.title("cmd_run --execute runs read-only task")
def test_cmd_run_execute(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_run(_ns(task="find baseUrl in sample.js", execute=True))
    out = capsys.readouterr().out
    assert code == 0
    assert "baseUrl" in out


@allure.story("Audit")
@allure.title("cmd_audit_context renders audit report")
def test_cmd_audit_context(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_audit_context(_ns())
    out = capsys.readouterr().out
    assert code == 0
    assert "Cursor context audit" in out


@allure.story("Tokens")
@allure.title("cmd_tokens counts files in workspace")
def test_cmd_tokens(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_tokens(_ns(paths=["projects/sample.js"]))
    out = capsys.readouterr().out
    assert code == 0
    assert "sample.js" in out


@allure.story("Tokens")
@allure.title("cmd_tokens returns error when no files found")
def test_cmd_tokens_no_files(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_tokens(_ns(paths=["missing-file.xyz"]))
    err = capsys.readouterr().err
    assert code == 1
    assert "No files found" in err


@allure.story("RAG")
@allure.title("cmd_rag searches docs with optional domain")
def test_cmd_rag(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_rag(_ns(query="baseUrl -D flag", domain="config", limit=5))
    out = capsys.readouterr().out
    assert code == 0
    assert "RAG hits" in out or "No RAG hits" in out


@allure.story("Compress")
@allure.title("cmd_compress reads stdin and prints dual format")
def test_cmd_compress_stdin(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("Fix baseUrl in configurator.\n"))
    code = cli.cmd_compress(_ns(ollama=False, raw=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "**Prompt:**" in out


@allure.story("Compress")
@allure.title("cmd_compress rejects empty stdin")
def test_cmd_compress_empty_stdin(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("   "))
    code = cli.cmd_compress(_ns(ollama=False, raw=False))
    err = capsys.readouterr().err
    assert code == 1
    assert "stdin" in err.lower()


@allure.story("Compress")
@allure.title("cmd_compress rejects oversized stdin")
def test_cmd_compress_too_large(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * (cli.COMPRESS_MAX_BYTES + 1)))
    code = cli.cmd_compress(_ns(ollama=False, raw=False))
    err = capsys.readouterr().err
    assert code == 1
    assert "too large" in err.lower()


@allure.story("Scripts")
@allure.title("cmd_scripts --list prints wrapper registry")
def test_cmd_scripts_list(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_scripts(_ns(list=True, run=None, args="", execute=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "check-meta-sync" in out


@allure.story("Scripts")
@allure.title("cmd_scripts --run dry-run prints command")
def test_cmd_scripts_run_dry(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_scripts(_ns(list=False, run="check-meta-sync", args="", execute=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "meta-sync-check" in out


@allure.story("Scripts")
@allure.title("cmd_scripts --run --execute runs read-only wrapper")
def test_cmd_scripts_run_execute(minimal_workspace: Path) -> None:
    from unittest.mock import MagicMock

    import subprocess

    with patch(
        "subprocess.run",
        return_value=MagicMock(spec=subprocess.CompletedProcess, returncode=0),
    ) as mock_run:
        code = cli.cmd_scripts(_ns(list=False, run="check-meta-sync", args="", execute=True))
    assert code == 0
    mock_run.assert_called_once()


@allure.story("Scripts")
@allure.title("cmd_scripts --run rejects non-read-only execute")
def test_cmd_scripts_run_refuse_write(minimal_workspace: Path, capsys) -> None:
    script = minimal_workspace / "scripts" / "migrate" / "phase1-rsync.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\necho rsync\n", encoding="utf-8")
    script.chmod(0o755)
    code = cli.cmd_scripts(_ns(list=False, run="phase1-rsync", args="", execute=True))
    err = capsys.readouterr().err
    assert code == 1
    assert "Refusing --execute" in err


@allure.story("Scripts")
@allure.title("cmd_scripts --run returns error for unknown wrapper")
def test_cmd_scripts_run_unknown(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_scripts(_ns(list=False, run="no-such", args="", execute=False))
    err = capsys.readouterr().err
    assert code == 1


@allure.story("Scripts")
@allure.title("cmd_scripts without list or run prints usage error")
def test_cmd_scripts_usage(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_scripts(_ns(list=False, run=None, args="", execute=False))
    err = capsys.readouterr().err
    assert code == 1


@allure.story("Scripts")
@allure.title("cmd_scripts --execute handles script timeout")
def test_cmd_scripts_run_timeout(minimal_workspace: Path, capsys) -> None:
    import subprocess

    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired("cmd", 120),
    ):
        code = cli.cmd_scripts(_ns(list=False, run="check-meta-sync", args="", execute=True))
    err = capsys.readouterr().err
    assert code == 124
    assert "timed out" in err.lower()


@allure.story("Report")
@allure.title("cmd_report prints text summary")
def test_cmd_report_text(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    log_file = tmp_path / "usage.jsonl"
    log_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log_file))
    code = cli.cmd_report(_ns(since="7d", json=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "No events" in out or "greedy-token usage" in out


@allure.story("Report")
@allure.title("cmd_report --json emits JSON summary")
def test_cmd_report_json(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    log_file = tmp_path / "usage.jsonl"
    log_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log_file))
    code = cli.cmd_report(_ns(since="7d", json=True))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert code == 0
    assert "events" in data


@allure.story("Config")
@allure.title("cmd_config prints Ollama settings")
def test_cmd_config_show(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_config(_ns(init=False, url=None, model=None, provider=None, force=False, export=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "url:" in out.lower() or "OLLAMA" in out


@allure.story("Config")
@allure.title("cmd_config --export prints shell exports")
def test_cmd_config_export(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_config(
        _ns(init=False, url=None, model=None, provider=None, force=False, export=True, reveal=False)
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "export OLLAMA_URL" in out


@allure.story("Config")
@allure.title("cmd_config --export masks CHEAP_LLM_API_KEY by default")
def test_cmd_config_export_masks_secret(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from greedy_token.settings import CheapLlmSettings

    secret = "sk-super-secret-value"
    monkeypatch.setattr(
        "greedy_token.cli.apply_ollama_env",
        lambda root: CheapLlmSettings(
            provider="openai_compat",
            url="https://api.example.com/v1",
            model="gpt-x",
            source="env",
            api_key=secret,
        ),
    )
    with allure.step("Run config --export without --reveal"):
        code = cli.cmd_config(
            _ns(init=False, url=None, model=None, provider=None, force=False, export=True, reveal=False)
        )
    captured = capsys.readouterr()
    with allure.step("Verify secret is masked and no history warning"):
        assert code == 0
        assert 'export CHEAP_LLM_API_KEY="***"' in captured.out
        assert secret not in captured.out
        assert secret not in captured.err
        assert "shell history" not in captured.err


@allure.story("Config")
@allure.title("cmd_config --export --reveal prints secret with a stderr warning")
def test_cmd_config_export_reveal(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from greedy_token.settings import CheapLlmSettings

    secret = "sk-super-secret-value"
    monkeypatch.setattr(
        "greedy_token.cli.apply_ollama_env",
        lambda root: CheapLlmSettings(
            provider="openai_compat",
            url="https://api.example.com/v1",
            model="gpt-x",
            source="env",
            api_key=secret,
        ),
    )
    with allure.step("Run config --export --reveal"):
        code = cli.cmd_config(
            _ns(init=False, url=None, model=None, provider=None, force=False, export=True, reveal=True)
        )
    captured = capsys.readouterr()
    with allure.step("Verify real secret printed and shell-history warning on stderr"):
        assert code == 0
        assert f'export CHEAP_LLM_API_KEY="{secret}"' in captured.out
        assert "***" not in captured.out
        assert "shell history" in captured.err


@allure.story("Config")
@allure.title("cmd_config --export --reveal without a key prints no warning")
def test_cmd_config_export_reveal_no_key(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from greedy_token.settings import CheapLlmSettings

    monkeypatch.setattr(
        "greedy_token.cli.apply_ollama_env",
        lambda root: CheapLlmSettings(
            provider="ollama",
            url="http://localhost:11434",
            model="qwen",
            source="default",
            api_key="",
        ),
    )
    with allure.step("Run config --export --reveal with no api_key"):
        code = cli.cmd_config(
            _ns(init=False, url=None, model=None, provider=None, force=False, export=True, reveal=True)
        )
    captured = capsys.readouterr()
    with allure.step("Verify no key line and no history warning"):
        assert code == 0
        assert "CHEAP_LLM_API_KEY" not in captured.out
        assert captured.err == ""


@allure.story("Config")
@allure.title("cmd_config --init works without workspace root (PyPI bootstrap)")
def test_cmd_config_init_without_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    monkeypatch.setattr(
        "greedy_token.cli.find_workspace_root",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    code = cli.cmd_config(
        _ns(
            init=True,
            url="http://localhost:11434",
            model="test-model",
            provider="ollama",
            force=True,
            export=False,
        )
    )
    out = capsys.readouterr().out
    assert code == 0
    assert cfg_path.is_file()
    assert "Created" in out
    assert "cheap llm settings" in out.lower() or "ollama" in out.lower()


@allure.story("Init")
@allure.title("cmd_init detect-only reports profile policy and tiers")
def test_cmd_init_detect_only(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda *a, **k: True)
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: Path("/nope/config.yaml"))
    code = cli.cmd_init(_ns(profile="solo", apply=False, force=False, json=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "profile:  solo" in out
    assert "policy: safe" in out
    assert "--apply" in out


@allure.story("Init")
@allure.title("cmd_init --json returns detection payload")
def test_cmd_init_json(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda *a, **k: False)
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: Path("/nope/config.yaml"))
    code = cli.cmd_init(_ns(profile="ci", apply=False, force=False, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["profile"] == "ci"
    assert payload["recommended_policy"] == "cheap_only"
    assert payload["ollama"] is False


@allure.story("Init")
@allure.title("cmd_init --apply writes config with profile policy")
def test_cmd_init_apply_writes_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import yaml

    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda *a, **k: True)
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    code = cli.cmd_init(_ns(profile="team", apply=True, force=True, json=False))
    out = capsys.readouterr().out
    assert code == 0
    assert cfg_path.is_file()
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["llm"]["policy"] == "hybrid"
    assert "Wrote" in out


@allure.story("Init")
@allure.title("cmd_init rejects unknown profile")
def test_cmd_init_bad_profile(capsys) -> None:
    code = cli.cmd_init(_ns(profile="galaxy", apply=False, force=False, json=False))
    assert code == 2


@allure.story("Init routes")
@allure.title("cmd_init --routes-from merges routes into the workspace config")
def test_cmd_init_routes_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import yaml

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(ws))
    src = tmp_path / "team-routes.yaml"
    src.write_text(
        "routes:\n"
        "  - stray-string\n"
        "  - id: team-jq\n    target: tool\n    tool: jq\n    patterns: [jq]\n"
        "cursor_fallback:\n  message: team\n",
        encoding="utf-8",
    )

    code = cli.cmd_init(
        _ns(profile="solo", apply=False, force=False, json=False, routes_from=str(src))
    )
    out = capsys.readouterr().out
    attach_text("stdout", out)
    assert code == 0
    assert "team-jq" in out
    cfg = yaml.safe_load((ws / ".greedy-token.yaml").read_text(encoding="utf-8"))
    assert [r["id"] for r in cfg["routes"]] == ["team-jq"]
    assert cfg["cursor_fallback"]["message"] == "team"

    with allure.step("Re-run is idempotent (merge by id, no duplicates) + --json payload"):
        code = cli.cmd_init(
            _ns(profile="solo", apply=False, force=False, json=True, routes_from=str(src))
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["routes"] == ["team-jq"]
        cfg = yaml.safe_load((ws / ".greedy-token.yaml").read_text(encoding="utf-8"))
        assert [r["id"] for r in cfg["routes"]] == ["team-jq"]


@allure.story("Init routes")
@allure.title("cmd_init --routes-from rejects missing file and files without routes")
def test_cmd_init_routes_from_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(ws))

    with allure.step("Missing file → exit 2"):
        code = cli.cmd_init(
            _ns(
                profile="solo",
                apply=False,
                force=False,
                json=False,
                routes_from=str(tmp_path / "missing.yaml"),
            )
        )
        assert code == 2
        assert "not found" in capsys.readouterr().err

    with allure.step("YAML without routes: section → exit 2"):
        bad = tmp_path / "no-routes.yaml"
        bad.write_text("- just\n- a-list\n", encoding="utf-8")
        code = cli.cmd_init(
            _ns(profile="solo", apply=False, force=False, json=False, routes_from=str(bad))
        )
        assert code == 2
        assert "No routes" in capsys.readouterr().err


@allure.story("Init routes")
@allure.title("cmd_init --routes-scaffold writes tool-rg-search with detected folders")
def test_cmd_init_routes_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import yaml

    ws = tmp_path / "ws"
    (ws / "backend").mkdir(parents=True)
    (ws / "frontend").mkdir()
    (ws / "node_modules").mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(ws))

    code = cli.cmd_init(
        _ns(profile="solo", apply=False, force=False, json=False, routes_scaffold=True)
    )
    out = capsys.readouterr().out
    attach_text("stdout", out)
    assert code == 0
    assert "tool-rg-search" in out
    cfg = yaml.safe_load((ws / ".greedy-token.yaml").read_text(encoding="utf-8"))
    route = cfg["routes"][0]
    assert route["id"] == "tool-rg-search"
    assert route["search_paths"] == ["backend", "frontend"]


@allure.story("Init routes")
@allure.title("cmd_init routes fall back to cwd when no workspace root is found")
def test_cmd_init_routes_cwd_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import yaml

    def no_root() -> Path:
        raise SystemExit("Cannot find workspace root")

    monkeypatch.setattr(cli, "find_workspace_root", no_root)
    project = tmp_path / "plain-project"
    (project / "lib").mkdir(parents=True)
    monkeypatch.chdir(project)

    code = cli.cmd_init(
        _ns(profile="solo", apply=False, force=False, json=True, routes_scaffold=True)
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["root"] == str(project)
    cfg = yaml.safe_load((project / ".greedy-token.yaml").read_text(encoding="utf-8"))
    assert cfg["routes"][0]["search_paths"] == ["lib"]


@allure.story("Init")
@allure.title("cmd_init surfaces install hints when ripgrep and ollama are missing")
def test_cmd_init_detect_missing_tools(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda *a, **k: False)
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: Path("/nope/config.yaml"))
    code = cli.cmd_init(_ns(profile="solo", apply=False, force=False, json=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "install ripgrep" in out
    assert "ollama offline" in out


@allure.story("Init")
@allure.title("cmd_init --apply refuses to overwrite an existing config without --force")
def test_cmd_init_apply_config_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("llm: {}\n", encoding="utf-8")
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda *a, **k: True)
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    code = cli.cmd_init(_ns(profile="team", apply=True, force=False, json=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "Config exists" in out


@allure.story("Init")
@allure.title("cmd_init --apply returns 1 when writing the config fails")
def test_cmd_init_apply_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    cfg_path = tmp_path / "config.yaml"  # absent → config_exists False
    monkeypatch.setattr("greedy_token.wrappers.ollama_available", lambda *a, **k: True)
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("greedy_token.settings.init_user_config", boom)
    code = cli.cmd_init(_ns(profile="team", apply=True, force=True, json=False))
    err = capsys.readouterr().err
    assert code == 1
    assert "disk full" in err


@allure.story("Run")
@allure.title("cmd_run --execute on cursor route exits non-zero with guidance")
def test_cmd_run_execute_cursor_refused(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_run(
        _ns(task="refactor monolithic header shell layout", execute=True)
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "Refusing --execute" in out or "cursor" in out.lower()


@allure.story("Config")
@allure.title("cmd_config show requires workspace when not --init")
def test_cmd_config_show_requires_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)
    monkeypatch.setattr(
        "greedy_token.cli.find_workspace_root",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    with pytest.raises(SystemExit):
        cli.cmd_config(_ns(init=False, url=None, model=None, provider=None, force=False, export=False))


@allure.story("Config")
@allure.title("cmd_config --init creates user config")
def test_cmd_config_init(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    code = cli.cmd_config(_ns(init=True, url="http://x:11434", model="m", provider=None, force=False, export=False))
    out = capsys.readouterr().out
    assert code == 0
    assert cfg_path.is_file()
    assert "Created" in out


@allure.story("Config")
@allure.title("cmd_config --init returns error when config exists")
def test_cmd_config_init_exists(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("ollama:\n  url: x\n  model: y\n", encoding="utf-8")
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    code = cli.cmd_config(_ns(init=True, url=None, model=None, provider=None, force=False, export=False))
    err = capsys.readouterr().err
    assert code == 1
    assert "already exists" in err.lower()


@allure.story("Config")
@allure.title("cmd_config --list-presets prints preset names")
def test_cmd_config_list_presets(capsys) -> None:
    code = cli.cmd_config(_ns(list_presets=True, init=False, export=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "local-ollama" in out
    assert "cursor-like-catalog" in out


@allure.story("Config")
@allure.title("cmd_config --init --preset local-ollama")
def test_cmd_config_init_preset(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    code = cli.cmd_config(
        _ns(init=True, preset="local-ollama", url=None, model=None, provider=None, force=True, export=False)
    )
    out = capsys.readouterr().out
    assert code == 0
    assert cfg_path.is_file()
    assert "Created" in out
    assert "ollama-fast" in cfg_path.read_text(encoding="utf-8")


@allure.story("Pipeline")
@allure.title("cmd_pipeline --list prints recipes")
def test_cmd_pipeline_list(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_pipeline(_ns(list=True, task="", execute=False, continue_on_error=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "meta-audit" in out or "Named" in out


@allure.story("Pipeline")
@allure.title("cmd_pipeline dry-run returns zero on success")
def test_cmd_pipeline_dry_run(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_pipeline(
        _ns(list=False, task="check-meta-sync then rag baseUrl", execute=False, continue_on_error=False)
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Per-step" in out or "dry-run" in out.lower() or "check-meta-sync" in out


@allure.story("Main")
@allure.title("main raises SystemExit with handler return code")
def test_main_raises_system_exit(minimal_workspace: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["route", "find baseUrl in sample.js"])
    assert exc.value.code == 0


@allure.story("Main")
@allure.title("main skips apply_ollama_env for config subcommand")
def test_main_config_skips_ollama_env(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"apply": False}

    def fake_apply(*args, **kwargs):
        called["apply"] = True
        from greedy_token.settings import OllamaSettings

        return OllamaSettings(url="http://localhost:11434", model="m", source="default")

    def fake_config(args: object) -> int:
        # Isolate main()'s pre-dispatch gate from cmd_config's own apply_ollama_env.
        return 0

    monkeypatch.setattr(cli, "apply_ollama_env", fake_apply)
    monkeypatch.setattr(cli, "cmd_config", fake_config)
    with pytest.raises(SystemExit) as exc:
        cli.main(["config"])
    assert exc.value.code == 0
    assert called["apply"] is False


@allure.story("Main")
@allure.title("main tolerates missing workspace during apply_ollama_env")
def test_main_tolerates_missing_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)

    def boom(*args, **kwargs):
        raise SystemExit("no root")

    monkeypatch.setattr(cli, "apply_ollama_env", boom)
    monkeypatch.setattr(cli, "find_workspace_root", lambda: (_ for _ in ()).throw(SystemExit("no root")))
    with pytest.raises(SystemExit):
        cli.main(["route", "find x"])
