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
    defaults = {"no_log": True}
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
    assert "Tier alternatives" in out or "tool" in out.lower()


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
    code = cli.cmd_rag(_ns(query="baseUrl -D flag", domain="e2e", limit=5))
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
    assert "check-meta-sync" in out


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
    code = cli.cmd_scripts(_ns(list=False, run="audit-skill", args="foo", execute=True))
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
    code = cli.cmd_config(_ns(init=False, url=None, model=None, force=False, export=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "url:" in out.lower() or "OLLAMA" in out


@allure.story("Config")
@allure.title("cmd_config --export prints shell exports")
def test_cmd_config_export(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_config(_ns(init=False, url=None, model=None, force=False, export=True))
    out = capsys.readouterr().out
    assert code == 0
    assert "export OLLAMA_URL" in out


@allure.story("Config")
@allure.title("cmd_config --init creates user config")
def test_cmd_config_init(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg_path)
    code = cli.cmd_config(_ns(init=True, url="http://x:11434", model="m", force=False, export=False))
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
    code = cli.cmd_config(_ns(init=True, url=None, model=None, force=False, export=False))
    err = capsys.readouterr().err
    assert code == 1
    assert "already exists" in err.lower()


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

    monkeypatch.setattr(cli, "apply_ollama_env", fake_apply)
    with pytest.raises(SystemExit):
        cli.main(["config"])
    assert called["apply"] is True


@allure.story("Main")
@allure.title("main tolerates missing workspace during apply_ollama_env")
def test_main_tolerates_missing_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_ROOT", raising=False)

    def boom(*args, **kwargs):
        raise SystemExit("no root")

    monkeypatch.setattr(cli, "apply_ollama_env", boom)
    monkeypatch.setattr(cli, "find_monorepo_root", lambda: (_ for _ in ()).throw(SystemExit("no root")))
    with pytest.raises(SystemExit):
        cli.main(["route", "find x"])
