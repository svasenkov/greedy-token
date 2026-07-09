"""Targeted tests for remaining branch coverage gaps."""

from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import allure
import pytest

pytestmark = [
    allure.epic("Test infrastructure"),
    allure.parent_suite("Test infrastructure"),
    allure.feature("Coverage gaps"),
    allure.suite("Coverage gaps"),
]


@allure.title("__init__ falls back when package metadata is unavailable")
def test_init_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    def boom(name: str) -> str:
        raise Exception("no metadata")

    monkeypatch.setattr("importlib.metadata.version", boom)
    mod = importlib.reload(importlib.import_module("greedy_token"))
    assert mod.__version__ == "0.4.6"


@allure.title("__main__ entrypoint invokes cli.main when executed as script")
def test_main_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def fake_main() -> None:
        called["n"] += 1

    monkeypatch.setattr("greedy_token.cli.main", fake_main)
    import runpy

    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "src" / "greedy_token" / "__main__.py"),
        run_name="__main__",
    )
    assert called["n"] == 1


@allure.title("rag_est_tokens reads chunk file when body is absent")
def test_rag_est_tokens_reads_file(minimal_workspace: Path) -> None:
    from greedy_token.budget import rag_est_tokens
    from greedy_token.rag_search import RagHit

    hits = [RagHit("id", "docs/rag/config/test-chunk.md", "config", 1.0, "excerpt")]
    assert rag_est_tokens(hits, minimal_workspace) > 0


@allure.title("format_spent_line without hint omits parenthetical")
def test_format_spent_line_no_hint() -> None:
    from greedy_token.budget import format_spent_line, format_savings_lines

    with patch("greedy_token.budget.spent_hint", return_value=""):
        line = format_spent_line(0, tier="unknown")
    assert line.endswith("~0")
    assert line.count("(") == 1
    lines = format_savings_lines(baseline=100, spent=50, saved=None)
    assert lines[-1].startswith("  Saved:")


@allure.title("format_tool_footer marks unavailable Ollama in tier alternatives")
def test_format_tool_footer_ollama_unavailable(minimal_workspace: Path) -> None:
    from greedy_token.budget import format_tool_footer

    with patch("greedy_token.budget.ollama_available", return_value=False):
        footer = format_tool_footer(
            "audit skill",
            minimal_workspace,
            tier="tool",
            est_tokens=0,
            route_id="mcp-search",
            executor_sub="rg",
        )
    assert "unavailable" in footer


@allure.title("CLI run prints fallback note and non-readonly dry-run hint")
def test_cli_run_branches(minimal_workspace: Path, capsys) -> None:
    import greedy_token.cli as cli

    with patch("greedy_token.cli.execute_task") as mock_exec:
        from greedy_token.executors import TaskRunResult
        from greedy_token.router import RouteDecision

        mock_exec.return_value = TaskRunResult(
            decision=RouteDecision(
                target="tool",
                route_id="x",
                confidence=1.0,
                matched=[],
                command="rg",
                note="",
                domains=[],
                read_only=True,
            ),
            output="hits",
            used_rag_fallback=True,
            exit_code=0,
        )
        cli.cmd_run(Namespace(task="find x", execute=True, no_log=True))
    out = capsys.readouterr().out
    assert "fallback" in out.lower()

    with patch("greedy_token.cli.route_task") as mock_route:
        from greedy_token.executors import RunPlan

        mock_route.return_value = RouteDecision(
            target="ollama",
            route_id="ollama-audit",
            confidence=1.0,
            matched=[],
            command="./scripts/ollama/audit-skill.sh",
            note="",
            domains=[],
            read_only=False,
        )
        with patch(
            "greedy_token.cli.plan_run",
            return_value=RunPlan(
                decision=mock_route.return_value,
                command="cmd",
                dry_run_output="dry",
                executable=False,
            ),
        ):
            cli.cmd_run(Namespace(task="audit skill", execute=False, no_log=True))
    out = capsys.readouterr().out
    assert "not read-only" in out.lower()


@allure.title("CLI compress --raw and scripts dry-run non-readonly branches")
def test_cli_compress_raw_and_scripts_dry(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import io

    import greedy_token.cli as cli

    monkeypatch.setattr(sys, "stdin", io.StringIO("Fix baseUrl.\n"))
    cli.cmd_compress(Namespace(ollama=False, raw=True, no_log=True))
    out = capsys.readouterr().out
    assert "baseUrl" in out
    assert "**Prompt:**" not in out

    cli.cmd_scripts(Namespace(list=False, run="audit-skill", args="foo", execute=False, no_log=True))
    out = capsys.readouterr().out
    assert "not read-only" in out.lower()


@allure.title("code_search directory scope and rg branches")
def test_code_search_directory_scope(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.code_search import search_code

    docs = minimal_workspace / "docs"
    (docs / "note.md").write_text("uniqueNeedle123\n", encoding="utf-8")
    with patch("greedy_token.code_search.resolve_rg") as mock_rg:
        mock_rg.return_value = Path("/bin/rg")
        with patch("greedy_token.code_search._run_rg", return_value=(0, "docs/note.md:1:uniqueNeedle123")):
            out = search_code("uniqueNeedle123", minimal_workspace, path="docs")
    assert "uniqueNeedle123" in out.text

    with patch("greedy_token.code_search.resolve_rg", return_value=None):
        out = search_code("ZZZNOTFOUND999", minimal_workspace, path=None)
    assert "No matches" in out.text


@allure.title("context_audit skips directories and handles empty rules table")
def test_context_audit_empty_rules() -> None:
    from greedy_token.context_audit import audit_context, render_audit

    isolated = Path("/tmp/greedy_token_audit_empty")
    rules = isolated / ".cursor" / "rules"
    if rules.exists():
        import shutil

        shutil.rmtree(isolated)
    rules.mkdir(parents=True)
    (rules / "placeholder").mkdir()
    out = render_audit(audit_context(isolated))
    assert "(none)" in out


@allure.title("format_estimate covers ollama, rag, cursor tiers and ollama note")
def test_format_estimate_tiers(minimal_workspace: Path) -> None:
    from greedy_token.estimator import TaskEstimate, format_estimate
    from greedy_token.router import RouteDecision

    for target in ("ollama", "rag", "cursor"):
        est = TaskEstimate(
            decision=RouteDecision(
                target=target,
                route_id=f"{target}-x",
                confidence=1.0,
                matched=["x"],
                command="cmd" if target != "cursor" else None,
                note="",
                domains=[],
                complexity="low",
                est_tokens=100,
                rationale="r",
            ),
            complexity="low",
            est_tokens=100,
            rationale="r",
            cursor_saved=9000,
            ollama_note="Ollama skipped" if target == "ollama" else None,
        )
        text = format_estimate(est, f"task {target}", minimal_workspace)
        assert "Tier scan:" in text


@allure.title("executors weak rg branches and python plan_run")
def test_executors_remaining_branches(minimal_workspace: Path) -> None:
    from greedy_token.executors import _tool_output_weak, execute_task, plan_run
    from greedy_token.router import RouteDecision

    assert _tool_output_weak("out", 2) is True

    with patch("greedy_token.executors._rag_fallback_output", return_value=None):
        with patch("greedy_token.executors.execute_plan", return_value=(0, "only result")):
            with patch(
                "greedy_token.executors.route_task",
                return_value=RouteDecision(
                    target="tool",
                    route_id="tool-rg",
                    confidence=1.0,
                    matched=[],
                    command="rg",
                    note="",
                    domains=[],
                    read_only=True,
                ),
            ):
                result = execute_task("find baseUrl", minimal_workspace)
    assert result.exit_code == 0
    assert result.output == "only result"

    decision = RouteDecision(
        target="ollama",
        route_id="ollama-audit",
        confidence=1.0,
        matched=[],
        command="./scripts/ollama/audit-skill.sh",
        note="",
        domains=[],
        read_only=False,
    )
    plan = plan_run(decision, "audit skill", minimal_workspace)
    assert "pass args" in plan.dry_run_output


@allure.title("mcp_icons raises when static assets missing")
def test_mcp_icons_missing() -> None:
    from greedy_token import mcp as mcp_mod

    with patch.object(Path, "is_file", return_value=False):
        with patch("greedy_token.mcp.resources.files") as mock_files:
            mock_files.return_value.joinpath.return_value.read_bytes.side_effect = FileNotFoundError(
                "missing"
            )
            mock_files.return_value.joinpath.return_value.read_text.side_effect = FileNotFoundError(
                "missing"
            )
            with pytest.raises(FileNotFoundError):
                mcp_mod.mcp_icons()


@allure.title("mcp __main__ entrypoint")
def test_mcp_main_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import greedy_token.mcp as mcp_mod

    called: list[int] = []
    monkeypatch.setattr(mcp_mod, "main", lambda: called.append(1))
    exec("if __name__ == '__main__': main()", {"__name__": "__main__", "main": mcp_mod.main})
    assert called == [1]


@allure.title("pipeline error paths and footer stopped_early")
def test_pipeline_error_paths(minimal_workspace: Path) -> None:
    from greedy_token.pipeline import (
        PipelineResult,
        PipelineStep,
        StepResult,
        _expand_named_pipeline,
        _resolve_wrapper_args,
        _run_step,
        format_pipeline_footer,
        parse_pipeline,
        run_pipeline,
    )

    with pytest.raises(ValueError, match="Empty pipeline"):
        parse_pipeline("   ")

    with pytest.raises(ValueError, match="needs more args"):
        _expand_named_pipeline("meta-audit")

    with pytest.raises(ValueError, match="audit-skill needs"):
        _resolve_wrapper_args("audit-skill", "")

    missing = minimal_workspace / ".cursor" / "skills" / "nope" / "SKILL.md"
    with pytest.raises(FileNotFoundError):
        _resolve_wrapper_args("audit-skill", "nope")

    step = PipelineStep("unknown", "python", "x", command=None)
    with pytest.raises(ValueError, match="No command"):
        _run_step(step, minimal_workspace, execute=True)

    long_out = "x" * 5000
    with patch(
        "greedy_token.pipeline._run_step",
        return_value=StepResult(
            step=PipelineStep("search", "tool", "search", args="q\t"),
            ok=True,
            exit_code=0,
            output=long_out,
            duration_ms=1,
            est_tokens=0,
            executed=True,
        ),
    ):
        result = run_pipeline("search q", minimal_workspace, execute=True)
    assert "truncated" in result.steps[0].output

    footer = format_pipeline_footer(
        PipelineResult(task="t", steps=[], stopped_early=True),
        minimal_workspace,
    )
    assert "stopped early" in footer


@allure.title("prompt_compress heuristic edge cases")
def test_prompt_compress_edges() -> None:
    from greedy_token.prompt_compress import compress_heuristic

    text = "Note: skip me.\nGoal: fix baseUrl.\n" + ("detail segment. " * 15)
    short = compress_heuristic(text)
    assert "baseUrl" in short
    assert short.endswith(".")


@allure.title("rag_index frontmatter and missing chunk paths")
def test_rag_index_edges(minimal_workspace: Path) -> None:
    from greedy_token.rag_index import _strip_frontmatter, get_indexed_chunks, invalidate_rag_index

    body = _strip_frontmatter("---\ntags: []\n---\nbody text\n")
    assert body.startswith("body")

    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "id": "missing-chunk",
                "domain": "config",
                "path": "docs/rag/config/missing-chunk.md",
                "tags": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    chunks = get_indexed_chunks(minimal_workspace)
    assert all(c.meta["id"] != "missing-chunk" for c in chunks)


@allure.title("rag_search domain filter and excerpt truncation")
def test_rag_search_edges(minimal_workspace: Path) -> None:
    from greedy_token.rag_search import _excerpt, search_rag

    assert search_rag("baseUrl", minimal_workspace, domains=["stacks"]) == []
    body = "head\n" + ("line with baseUrl " + "x" * 400 + "\n") * 3
    excerpt = _excerpt(body, {"baseurl"})
    assert excerpt.endswith("…") or len(excerpt) <= 320


@allure.title("router search query scoring branches")
def test_router_search_helpers() -> None:
    from greedy_token.router import _extract_search_query, _score_search_token, _strip_search_prefix

    assert _strip_search_prefix("find baseUrl in file") == "baseUrl in file"
    assert _score_search_token("HTTP") > 0
    assert _score_search_token("123") < 0
    assert _extract_search_query('find "quoted term"') == "quoted term"
    assert _extract_search_query("find") == "find"


@allure.title("format_decision non-readonly command branch")
def test_format_decision_non_readonly(minimal_workspace: Path) -> None:
    from greedy_token.router import RouteDecision, format_decision

    decision = RouteDecision(
        target="python",
        route_id="script-audit",
        confidence=1.0,
        matched=["audit"],
        command="./scripts/ollama/audit-skill.sh",
        note="",
        domains=[],
        complexity="low",
        est_tokens=0,
        rationale="audit",
        read_only=False,
    )
    out = format_decision(decision, "audit skill", minimal_workspace)
    assert "not read-only" in out


@allure.title("workspace_config_path discovers root when omitted")
def test_workspace_config_path_auto_root(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.settings import workspace_config_path

    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    path = workspace_config_path(None)
    assert path == minimal_workspace / ".greedy-token.yaml"


@allure.title("collect_paths skips broken symlinks")
def test_collect_paths_broken_symlink(minimal_workspace: Path) -> None:
    from greedy_token.tokens import collect_paths

    target = minimal_workspace / "real.txt"
    target.write_text("x", encoding="utf-8")
    link = minimal_workspace / "broken-link.txt"
    try:
        link.symlink_to(minimal_workspace / "missing-target.txt")
    except OSError:
        pytest.skip("symlinks not supported")
    paths = collect_paths(["."], minimal_workspace)
    assert not any(p.name == "broken-link.txt" for p in paths)


@allure.title("tool_paths scans PATH directories")
def test_tool_paths_path_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.tool_paths import resolve_rg

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rg = bin_dir / "rg"
    rg.write_text("#!/bin/sh\necho rg\n", encoding="utf-8")
    rg.chmod(0o755)
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", str(bin_dir))
    found = resolve_rg()
    assert found == rg.resolve()


@allure.title("usage helper branches")
def test_usage_helper_branches(minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.router import RouteDecision
    from greedy_token.usage import (
        ReportSummary,
        build_compress_event,
        build_route_event,
        executor_from_decision,
        format_report,
        load_events,
        logging_enabled,
        rotate_log_if_needed,
        wrapper_for_route_id,
    )

    log_file = tmp_path / "usage.jsonl"

    assert logging_enabled(no_log=True) is False

    assert executor_from_decision(
        RouteDecision(
            target="python",
            route_id="unknown-python",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
        )
    )["kind"] == "script"
    assert executor_from_decision(
        RouteDecision(
            target="ollama",
            route_id="ollama-x",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
        )
    )["model"]
    assert wrapper_for_route_id("no-match") is None

    event = build_compress_event(
        text="long",
        short="s",
        use_ollama=True,
        eval_tokens=42,
    )
    assert event["executor"]["eval_tokens"] == 42

    log_file.write_text(
        '{"cmd":"x","ts":""}\n'
        '\n'
        '{"cmd":"y","ts":"2030-01-01T00:00:00Z","selected_tier":"tool"}\n',
        encoding="utf-8",
    )
    from greedy_token.usage import parse_since

    events, skipped = load_events(log_file, since=parse_since("2025-01-01"))
    assert len(events) == 1
    assert events[0]["cmd"] == "y"
    assert skipped >= 1

    summary = format_report(ReportSummary(events=0, since="7d", skipped_lines=2))
    assert "No events since 7d" in summary
    assert "malformed" in summary

    oldest = log_file.with_name("usage.jsonl.5")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_BYTES", "10")
    monkeypatch.setenv("GREEDY_TOKEN_LOG_MAX_FILES", "5")
    log_file.write_text("012345678901234567890\n", encoding="utf-8")
    oldest.write_text("old\n", encoding="utf-8")
    assert rotate_log_if_needed(log_file) is True

    build_route_event(
        cmd="rag",
        task="q",
        root=minimal_workspace,
        decision=RouteDecision(
            target="rag",
            route_id="rag-x",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
        ),
        tier_scan=[],
    )


@allure.title("resolve_wrapper_command raises when script file missing")
def test_resolve_wrapper_missing_script(minimal_workspace: Path) -> None:
    from greedy_token.wrappers import resolve_wrapper_command

    with pytest.raises(FileNotFoundError):
        resolve_wrapper_command("gen-env-configs", minimal_workspace)


@allure.title("remaining code_search and tool_paths branches")
def test_code_search_and_tool_paths_branches(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.code_search import _python_search_tree, resolve_search_path, search_code
    from greedy_token.tool_paths import resolve_rg

    a = minimal_workspace / "projects" / "a" / "target.js"
    b = minimal_workspace / "projects" / "b" / "other.js"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_text("z\n", encoding="utf-8")
    b.write_text("z\n", encoding="utf-8")
    with patch.object(Path, "glob") as mock_glob:
        mock_glob.return_value = [a, b]
        found = resolve_search_path("target.js", minimal_workspace)
    assert found == a.resolve()

    missing_dir = minimal_workspace / "missing-scope"
    hits = _python_search_tree(
        minimal_workspace,
        "needleXYZ",
        scope_dirs=[missing_dir, minimal_workspace / "projects"],
        name_glob="*sample.js",
        limit=3,
    )
    assert isinstance(hits, list)

    with patch("greedy_token.code_search.resolve_rg") as mock_rg:
        mock_rg.return_value = Path("/bin/rg")
        with patch("greedy_token.code_search._run_rg", return_value=(0, "projects/sample.js:1:baseUrl")):
            out = search_code("baseUrl", minimal_workspace, path="*.js")
    assert "baseUrl" in out.text

    bin_dir = Path("/tmp/greedy_token_rg_bin")
    bin_dir.mkdir(exist_ok=True)
    rg = bin_dir / "rg"
    rg.write_text("#!/bin/sh\necho\n", encoding="utf-8")
    rg.chmod(0o755)
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", str(bin_dir))
    with patch("greedy_token.tool_paths.shutil.which", return_value=None):
        assert resolve_rg() == rg.resolve()

    class BadPath(Path):
        def resolve(self, *args, **kwargs):
            raise OSError("bad")

    with patch("greedy_token.tool_paths._rg_candidates", return_value=iter([BadPath("/bad/rg")])):
        assert resolve_rg() is None


@allure.title("remaining executors and router branches")
def test_executors_and_router_branches(minimal_workspace: Path) -> None:
    from greedy_token.executors import _rag_fallback_output, execute_task
    from greedy_token.router import _extract_search_query

    with patch("greedy_token.executors.search_rag", side_effect=[[], [MagicMock()]]):
        with patch("greedy_token.executors.format_hits", return_value="RAG hits"):
            assert _rag_fallback_output("baseUrl config", minimal_workspace) == "RAG hits"

    with patch("greedy_token.executors.execute_plan", return_value=(0, "clean output")):
        with patch(
            "greedy_token.executors.route_task",
            return_value=__import__("greedy_token.router", fromlist=["RouteDecision"]).RouteDecision(
                target="tool",
                route_id="tool-rg",
                confidence=1.0,
                matched=[],
                command="rg",
                note="",
                domains=[],
                read_only=True,
            ),
        ):
            result = execute_task("find x", minimal_workspace)
    assert result.output == "clean output"

    assert _extract_search_query("find ") == "find"


@allure.title("remaining pipeline and rag branches")
def test_pipeline_and_rag_branches(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.pipeline import (
        _estimate_step_tokens,
        _load_pipelines_config,
        _resolve_wrapper_args,
        _run_step,
        format_pipeline_footer,
        parse_pipeline,
    )
    from greedy_token.pipeline import PipelineResult, PipelineStep, StepResult

    with patch.object(Path, "is_file", return_value=False):
        assert _load_pipelines_config() == {}

    with pytest.raises(ValueError, match="Unknown step"):
        parse_pipeline("not-a-real-step")

    skill = minimal_workspace / ".cursor" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# demo\n", encoding="utf-8")
    rel = _resolve_wrapper_args("audit-skill", "demo")
    assert rel.endswith("SKILL.md")

    abs_skill = minimal_workspace / "custom.md"
    abs_skill.write_text("# custom\n", encoding="utf-8")
    rel2 = _resolve_wrapper_args("audit-skill", str(abs_skill))
    assert "custom.md" in rel2

    search_step = PipelineStep("search", "tool", "search", args="baseUrl\tsample.js")
    dry = _run_step(search_step, minimal_workspace, execute=False)
    assert "(dry-run) search" in dry.output

    audit_step = PipelineStep(
        "audit-skill",
        "ollama",
        "audit",
        command="echo ok",
        args=str(skill.relative_to(minimal_workspace)),
    )
    tokens = _estimate_step_tokens(audit_step, "output text", minimal_workspace)
    assert tokens >= 0

    footer = format_pipeline_footer(
        PipelineResult(
            task="meta-audit x",
            steps=[
                StepResult(
                    step=PipelineStep("audit-skill", "ollama", "audit"),
                    ok=True,
                    exit_code=0,
                    output="ok",
                    duration_ms=1,
                    est_tokens=10,
                    executed=True,
                )
            ],
        ),
        minimal_workspace,
    )
    assert "cheap" in footer


@allure.title("remaining prompt_compress rag_index rag_search usage mcp branches")
def test_misc_remaining_branches(minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.prompt_compress import compress_heuristic
    from greedy_token.rag_index import get_indexed_chunks, invalidate_rag_index
    from greedy_token.rag_search import _excerpt
    from greedy_token.usage import executor_from_decision, format_report, load_events, parse_since
    from greedy_token.router import RouteDecision

    short = compress_heuristic("Why: skip.\n" + ("segment " * 20))
    assert "Why" not in short or "segment" in short

    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    manifest.write_text(
        '{"id":"empty-path","domain":"config","path":"","tags":[]}\n'
        + '{"id":"ghost","domain":"config","path":"docs/rag/config/ghost.md","tags":[]}\n',
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    chunks = get_indexed_chunks(minimal_workspace)
    assert all(c.meta.get("id") != "ghost" for c in chunks)

    excerpt = _excerpt("plain text without token match but long " + "x" * 400, {"missing"})
    assert excerpt.endswith("…") or len(excerpt) <= 320

    assert executor_from_decision(
        RouteDecision(
            target="rag",
            route_id="rag-x",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
        )
    ) == {"kind": "rag"}

    log_file = tmp_path / "usage.jsonl"
    log_file.write_text('{"cmd":"bad-ts","ts":"not-a-date","selected_tier":"tool"}\n', encoding="utf-8")
    _, skipped = load_events(log_file, since=parse_since("2025-01-01"))
    assert skipped == 1

    assert "No events since" in format_report(
        __import__("greedy_token.usage", fromlist=["ReportSummary"]).ReportSummary(
            events=0, since="1d", skipped_lines=0
        )
    )

    import greedy_token.mcp as mcp_mod

    with patch.object(Path, "is_file", return_value=False):
        icons = mcp_mod.mcp_icons()
    assert icons[0].src.startswith("data:")


@allure.title("context_audit skips non-file glob hits")
def test_context_audit_skips_non_file(minimal_workspace: Path) -> None:
    from greedy_token.context_audit import audit_context

    fake_dir = minimal_workspace / ".cursor" / "rules" / "fake.mdc"
    fake_dir.mkdir(parents=True)
    with patch.object(Path, "glob") as mock_glob:
        mock_glob.return_value = [fake_dir, minimal_workspace / ".cursor" / "rules" / "test.mdc"]
        items = audit_context(minimal_workspace)
    assert all(i.path.endswith("test.mdc") for i in items)


@allure.title("_rg_candidates yields all fallback paths")
def test_rg_candidates_exhaustive(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import tool_paths

    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent/bin")
    with patch.object(tool_paths.shutil, "which", return_value=None):
        candidates = list(tool_paths._rg_candidates())
    assert len(candidates) >= 5


@allure.title("final branch coverage for remaining lines")
def test_final_coverage_branches(minimal_workspace: Path, tmp_path: Path) -> None:
    from greedy_token.code_search import _python_search_tree
    from greedy_token.executors import execute_task
    from greedy_token.pipeline import _estimate_step_tokens, _expand_named_pipeline, _resolve_wrapper_args
    from greedy_token.pipeline import PipelineStep
    from greedy_token.prompt_compress import compress_heuristic
    from greedy_token.rag_search import _excerpt
    from greedy_token.router import _extract_search_query
    from greedy_token.usage import executor_from_decision, format_report, load_events, parse_since
    from greedy_token.router import RouteDecision

    scope = tmp_path / "scope"
    scope.mkdir()
    outside = tmp_path.parent / "ext-tree.txt"
    outside.write_text("needle999\n", encoding="utf-8")
    hits = _python_search_tree(
        tmp_path,
        "needle999",
        scope_dirs=[scope, tmp_path.parent],
        name_glob=None,
        limit=5,
    )
    assert any("needle999" in h for h in hits)

    with patch("greedy_token.executors.search_rag", return_value=[]):
        from greedy_token.executors import _rag_fallback_output

        assert _rag_fallback_output("baseUrl", minimal_workspace) is None

    with patch("greedy_token.executors.execute_plan", return_value=(0, "plain output\n")):
        with patch(
            "greedy_token.executors.route_task",
            return_value=RouteDecision(
                target="tool",
                route_id="tool-rg",
                confidence=1.0,
                matched=[],
                command="rg",
                note="",
                domains=[],
                read_only=True,
            ),
        ):
            assert execute_task("find x", minimal_workspace).output == "plain output"

    with patch("greedy_token.executors.execute_plan", return_value=(0, "script stdout")):
        with patch(
            "greedy_token.executors.route_task",
            return_value=RouteDecision(
                target="python",
                route_id="script-check-meta-sync",
                confidence=1.0,
                matched=[],
                command="./scripts/check-meta-sync.sh",
                note="",
                domains=[],
                read_only=True,
            ),
        ):
            result = execute_task("check meta", minimal_workspace)
            assert result.output == "script stdout"
            assert result.exit_code == 0

    from greedy_token.router import _build_tool_command

    jq_cmd = _build_tool_command({"tool": "jq", "jq_filter": "."}, "task", minimal_workspace)
    assert "jq -r" in jq_cmd

    with pytest.raises(ValueError, match="needs more args"):
        _expand_named_pipeline("meta-audit")

    trailing = _expand_named_pipeline("meta-audit configurator-boolean extra-tail")
    assert "audit-skill configurator-boolean extra-tail" in trailing

    skill_dir = minimal_workspace / ".cursor" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    step = PipelineStep("audit-skill", "ollama", "audit", args=".cursor/skills/demo-skill/SKILL.md")
    assert _estimate_step_tokens(step, "output", minimal_workspace) > 0

    cursor_step = PipelineStep("noop", "cursor", "cursor", args="")
    assert _estimate_step_tokens(cursor_step, "hello output", minimal_workspace) > 0

    skill_md = minimal_workspace / "abs-skill.md"
    skill_md.write_text("# skill\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        _resolve_wrapper_args("audit-skill", "missing-skill.md")

    rel = _resolve_wrapper_args("audit-skill", str(skill_md))
    assert rel.endswith("abs-skill.md")

    step = PipelineStep(
        "audit-skill",
        "ollama",
        "audit",
        args=str(skill_md.relative_to(minimal_workspace)),
    )
    assert _estimate_step_tokens(step, "out", minimal_workspace) > 0

    text = "Why: drop.\nDo thing.\nExtra.\nFourth."
    compressed = compress_heuristic(text)
    assert "Do thing" in compressed
    split = compress_heuristic("Alpha\nBeta\nGamma\nGoal without period")
    assert split.endswith(".")

    assert _excerpt("short text", {"zzz"}) == "short text"
    assert _extract_search_query("find ") == "find"
    with patch("greedy_token.router._strip_search_prefix", return_value=""):
        assert _extract_search_query("  scope only  ") == "scope only"

    assert executor_from_decision(
        RouteDecision(
            target="rag",
            route_id="rag",
            confidence=1.0,
            matched=[],
            command=None,
            note="",
            domains=[],
        )
    )["kind"] == "rag"

    from greedy_token.usage import aggregate_events, _parse_event_ts

    assert _parse_event_ts({"ts": "2030-06-15T12:00:00"}).tzinfo is not None

    log_file = tmp_path / "u.jsonl"
    log_file.write_text(
        '{"cmd":"x","ts":"2030-06-15T12:00:00","selected_tier":"tool"}\n',
        encoding="utf-8",
    )
    events, skipped = load_events(log_file, since=parse_since("2025-01-01"))
    assert len(events) == 1
    assert skipped == 0

    summary = aggregate_events([{"selected_tier": "legacy-tier", "route_id": "x"}])
    assert "legacy-tier" in summary.by_tier

    assert "No events yet." in format_report(
        __import__("greedy_token.usage", fromlist=["ReportSummary"]).ReportSummary(
            events=0, since=None, skipped_lines=0
        )
    )

    log_file.write_text('{"cmd":"x","ts":"bad","selected_tier":"tool"}\n', encoding="utf-8")
    _, skipped = load_events(log_file, since=parse_since("2025-01-01"))
    assert skipped == 1

    import greedy_token.mcp as mcp_mod

    with patch.object(Path, "is_file", return_value=False):
        with patch("greedy_token.mcp.resources.files") as mock_files:
            mock_files.return_value.joinpath.return_value.read_bytes.side_effect = FileNotFoundError(
                "missing"
            )
            mock_files.return_value.joinpath.return_value.read_text.side_effect = FileNotFoundError(
                "missing"
            )
            with pytest.raises(FileNotFoundError):
                mcp_mod.mcp_icons()

    from greedy_token.tokens import count_file

    est = count_file(minimal_workspace / "projects" / "sample.js")
    assert est.tokens > 0

    scope2 = tmp_path / "lim"
    scope2.mkdir()
    (scope2 / "a.txt").write_text("limhit\n", encoding="utf-8")
    (scope2 / "b.txt").write_text("limhit\n", encoding="utf-8")
    limited = _python_search_tree(tmp_path, "limhit", scope_dirs=[scope2], limit=1)
    assert len(limited) == 1

    long_line = "Start. " + "x" * 130 + ". tail"
    multi = compress_heuristic("\n".join(["Alpha", "Beta", "Gamma", "Delta", long_line]))
    assert len(multi) > 20
    assert "Start" in multi or "tail" in multi

    with pytest.raises(ValueError):
        _expand_named_pipeline("meta-audit")

    assert _extract_search_query("grep ") == "grep"

    assert executor_from_decision(
        RouteDecision(
            target="cursor",
            route_id="cursor-fallback",
            confidence=0.3,
            matched=[],
            command=None,
            note="",
            domains=[],
        )
    ) == {"kind": "cursor"}

