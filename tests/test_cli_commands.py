from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import allure
import pytest

from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("CLI"),
    allure.parent_suite("CLI"),
    allure.feature("CLI commands"),
    allure.suite("CLI commands"),
]


def _run_cli(
    *args: str,
    workspace: Path,
    extra_env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GREEDY_TOKEN_ROOT": str(workspace)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "greedy_token", "--no-log", *args],
        capture_output=True,
        text=True,
        env=env,
        input=input_text,
    )


@allure.story("Estimate")
@allure.title("CLI estimate prints tier scan and token estimate")
def test_cli_estimate_shows_tier_scan(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token estimate"):
        proc = _run_cli("estimate", "find baseUrl in sample.js", workspace=minimal_workspace)
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify tier scan and task context in output"):
        assert proc.returncode == 0
        assert "Tier scan:" in proc.stdout
        assert "← selected" in proc.stdout
        assert "baseUrl" in proc.stdout


@allure.story("Run")
@allure.title("CLI run dry-run prints plan without executing")
def test_cli_run_dry_run_shows_plan(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token run dry-run"):
        proc = _run_cli("run", "find baseUrl in sample.js", workspace=minimal_workspace)
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify plan shows route and execute hint"):
        assert proc.returncode == 0
        assert "Route:" in proc.stdout
        assert "tool" in proc.stdout.lower()
        assert "--execute" in proc.stdout or "dry-run" in proc.stdout.lower()


@allure.story("Run")
@allure.title("CLI run --execute runs read-only find task")
def test_cli_run_execute_find_task(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token run --execute"):
        proc = _run_cli(
            "run",
            "find baseUrl in sample.js",
            "--execute",
            workspace=minimal_workspace,
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify search finds baseUrl"):
        assert proc.returncode == 0
        assert "baseUrl" in proc.stdout


@allure.story("Report")
@allure.title("CLI report --json returns aggregate summary JSON")
def test_cli_report_json(tmp_path: Path, minimal_workspace: Path) -> None:
    log_file = tmp_path / "usage.jsonl"
    log_file.write_text("", encoding="utf-8")
    with allure.step("Run greedy-token report --json"):
        proc = _run_cli(
            "report",
            "--since",
            "7d",
            "--json",
            workspace=minimal_workspace,
            extra_env={"GREEDY_TOKEN_LOG": str(log_file)},
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify JSON summary has zero events"):
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        attach_json("report summary", data)
        assert "events" in data
        assert data["events"] == 0


@allure.story("Config")
@allure.title("CLI config shows Ollama settings")
def test_cli_config_shows_settings(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token config"):
        proc = _run_cli("config", workspace=minimal_workspace)
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify Ollama settings are shown"):
        assert proc.returncode == 0
        assert "ollama" in proc.stdout.lower() or "OLLAMA" in proc.stdout


@allure.story("Config")
@allure.title("CLI config --init creates user config under HOME")
def test_cli_config_init(tmp_path: Path, minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token config --init"):
        proc = _run_cli(
            "config",
            "--init",
            "--url",
            "http://localhost:11434",
            "--model",
            "test-model",
            workspace=minimal_workspace,
            extra_env={"HOME": str(tmp_path)},
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify user config file was created"):
        assert proc.returncode == 0
        assert "Created" in proc.stdout
        assert (tmp_path / ".greedy-token" / "config.yaml").is_file()


@allure.story("Tokens")
@allure.title("CLI tokens counts tokens in workspace file")
def test_cli_tokens_counts_file(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token tokens on sample.js"):
        proc = _run_cli("tokens", "projects/sample.js", workspace=minimal_workspace)
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify token count output"):
        assert proc.returncode == 0
        assert "sample.js" in proc.stdout
        assert "tokens" in proc.stdout.lower()


@allure.story("Audit context")
@allure.title("CLI audit-context renders rules token audit")
def test_cli_audit_context(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token audit-context"):
        proc = _run_cli("audit-context", workspace=minimal_workspace)
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify context audit report sections"):
        assert proc.returncode == 0
        assert "Cursor context audit" in proc.stdout
        assert "Always-on rules" in proc.stdout


@allure.story("RAG")
@allure.title("CLI rag searches docs/rag chunks")
def test_cli_rag_query(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token rag query"):
        proc = _run_cli(
            "rag",
            "baseUrl -D flag",
            "--domain",
            "config",
            workspace=minimal_workspace,
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify RAG hits in output"):
        assert proc.returncode == 0
        assert "RAG hits" in proc.stdout or "test-baseurl" in proc.stdout


@allure.story("Scripts")
@allure.title("CLI scripts --list shows wrapper registry")
def test_cli_scripts_list(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token scripts --list"):
        proc = _run_cli("scripts", "--list", workspace=minimal_workspace)
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify check-meta-sync wrapper is listed"):
        assert proc.returncode == 0
        assert "check-meta-sync" in proc.stdout


@allure.story("Pipeline")
@allure.title("CLI pipeline --execute runs search and RAG steps")
def test_cli_pipeline_execute_search_rag(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token pipeline --execute"):
        proc = _run_cli(
            "pipeline",
            "search baseUrl\tsample.js then rag baseUrl",
            "--execute",
            workspace=minimal_workspace,
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify search and RAG results in output"):
        assert proc.returncode == 0
        assert "baseUrl" in proc.stdout
        assert "RAG hits" in proc.stdout or "test-baseurl" in proc.stdout


@allure.story("Scripts")
@allure.title("CLI scripts --run check-meta-sync --execute runs wrapper")
def test_cli_scripts_run_execute_check_meta_sync(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token scripts --run check-meta-sync --execute"):
        proc = _run_cli(
            "scripts",
            "--run",
            "check-meta-sync",
            "--execute",
            workspace=minimal_workspace,
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify wrapper script output"):
        assert proc.returncode == 0
        assert "meta-sync-check-ok" in proc.stdout


@allure.story("Compress")
@allure.title("CLI compress reads stdin and prints short prompt")
def test_cli_compress_stdin(minimal_workspace: Path) -> None:
    with allure.step("Run greedy-token compress with stdin input"):
        proc = _run_cli(
            "compress",
            "--raw",
            workspace=minimal_workspace,
            input_text="Fix baseUrl in configurator.\n",
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify compressed prompt retains baseUrl"):
        assert proc.returncode == 0
        assert "baseUrl" in proc.stdout
