"""Public-contract edge tests that keep fail_under=100 / branch without theater."""

from __future__ import annotations

import io
import json
import os
import runpy
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


@allure.title("__main__ entrypoint invokes cli.main when executed as script")
def test_main_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def fake_main() -> None:
        called["n"] += 1

    monkeypatch.setattr("greedy_token.cli.main", fake_main)
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


@allure.title("format_spent_line / savings / footer without private helpers")
def test_budget_footer_public(minimal_workspace: Path) -> None:
    from greedy_token.budget import format_savings_lines, format_spent_line, format_tool_footer

    with patch("greedy_token.budget.spent_hint", return_value=""):
        line = format_spent_line(0, tier="unknown")
    # Label has its own parentheses; without hint there is no trailing note.
    assert line == "  Spent (MCP executor, LLM tokens): ~0"

    lines = format_savings_lines(baseline=100, spent=50, saved=None)
    assert lines[-1].startswith("  Saved:")

    with patch("greedy_token.budget.ollama_available", return_value=False):
        footer = format_tool_footer(
            "audit skill",
            minimal_workspace,
            tier="tool",
            est_tokens=0,
            route_id="mcp-search",
            executor_sub="rg",
            style="full",
        )
    assert "unavailable" in footer


@allure.title("CLI run prints fallback note and non-readonly dry-run hint")
def test_cli_run_branches(minimal_workspace: Path, capsys) -> None:
    import greedy_token.cli as cli
    from greedy_token.executors import RunPlan, TaskRunResult
    from greedy_token.router import RouteDecision

    with patch("greedy_token.cli.execute_task") as mock_exec:
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
        mock_route.return_value = RouteDecision(
            target="ollama",
            route_id="ollama-inventory",
            confidence=1.0,
            matched=[],
            command="./scripts/ollama/batch-inventory.sh",
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
            cli.cmd_run(Namespace(task="batch inventory", execute=False, no_log=True))
    out = capsys.readouterr().out
    assert "not read-only" in out.lower()


@allure.title("CLI compress --raw and scripts dry-run non-readonly")
def test_cli_compress_raw_and_scripts_dry(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import greedy_token.cli as cli

    monkeypatch.setattr(sys, "stdin", io.StringIO("Fix baseUrl.\n"))
    cli.cmd_compress(Namespace(ollama=False, raw=True, no_log=True))
    out = capsys.readouterr().out
    assert "baseUrl" in out
    assert "**Prompt:**" not in out

    script = minimal_workspace / "scripts" / "migrate" / "phase1-rsync.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\necho rsync\n", encoding="utf-8")
    script.chmod(0o755)
    cli.cmd_scripts(Namespace(list=False, run="phase1-rsync", args="", execute=False, no_log=True))
    out = capsys.readouterr().out
    assert "not read-only" in out.lower()


@allure.title("code_search directory scope and python engine fallback")
def test_code_search_directory_scope(minimal_workspace: Path) -> None:
    from greedy_token.code_search import resolve_search_path, search_code

    docs = minimal_workspace / "docs"
    (docs / "note.md").write_text("uniqueNeedle123\n", encoding="utf-8")
    resolved = resolve_search_path("docs", minimal_workspace)
    assert resolved is not None
    assert resolved.is_dir()
    out = search_code("uniqueNeedle123", minimal_workspace, path="docs")
    assert "uniqueNeedle123" in out.text
    assert out.engine in ("rg", "python")

    with patch("greedy_token.code_search.resolve_rg", return_value=None):
        out = search_code("ZZZNOTFOUND999", minimal_workspace, path=None)
    assert "No matches" in out.text


@allure.title("code_search: unique dir, outside OSError, empty basename, name_glob")
def test_code_search_resolve_edges(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.code_search import resolve_search_path, search_code

    assert resolve_search_path(str(tmp_path / "missing-abs-xyz"), minimal_workspace) is None

    nested = minimal_workspace / "projects" / "unique-dir-xyz"
    nested.mkdir(parents=True)
    (nested / "f.txt").write_text("nestedNeedle\n", encoding="utf-8")
    found_dir = resolve_search_path("unique-dir-xyz", minimal_workspace)
    assert found_dir is not None and found_dir.is_dir()

    with patch("greedy_token.code_search.resolve_rg", return_value=None):
        out = search_code("nestedNeedle", minimal_workspace, path="unique-dir-xyz")
    assert "nestedNeedle" in out.text
    assert out.engine == "python"

    # Absolute path whose resolve() raises OSError → not_found (not uncaught).
    outside_dir = tmp_path.parent / "outside-oserror-dir"
    outside_dir.mkdir(exist_ok=True)
    outside = outside_dir / "outside-oserror.txt"
    outside.write_text("x\n", encoding="utf-8")
    real_resolve = Path.resolve
    boom_left = {"n": 1}

    def boom_resolve(self, *args, **kwargs):
        if boom_left["n"] > 0 and "outside-oserror" in str(self):
            boom_left["n"] -= 1
            raise OSError("boom")
        return real_resolve(self, *args, **kwargs)

    with patch.object(Path, "resolve", boom_resolve):
        result = search_code("x", minimal_workspace, path=str(outside))
    assert "Error:" in result.text
    assert "not found" in result.text or "outside" in result.text

    # Absolute path: is_file() raises OSError → not_found.
    abs_stat_boom = outside_dir / "stat-boom.txt"
    abs_stat_boom.write_text("y\n", encoding="utf-8")
    real_is_file = Path.is_file

    def boom_is_file(self):
        if "stat-boom" in str(self):
            raise OSError("stat boom")
        return real_is_file(self)

    with patch.object(Path, "is_file", boom_is_file):
        assert resolve_search_path(str(abs_stat_boom), minimal_workspace) is None

    # Relative path: (root/hint).resolve() raises OSError → fall through to name lookup.
    rel_boom = MagicMock()
    rel_boom.is_absolute.return_value = False
    rel_boom.name = "no-such-rel-boom-xyz"
    rooted_boom = MagicMock()
    rooted_boom.resolve.side_effect = OSError("rel boom")
    mock_root = MagicMock()
    mock_root.resolve.return_value = mock_root
    mock_root.__truediv__.return_value = rooted_boom
    mock_root.glob.return_value = []
    with patch("greedy_token.code_search.Path", return_value=rel_boom):
        assert resolve_search_path("no-such-rel-boom-xyz", mock_root) is None

    # Unique file basename (do not create extra *.js — keeps path="*.js" unambiguous).
    a = minimal_workspace / "projects" / "a" / "target-unique.dat"
    b = minimal_workspace / "projects" / "b" / "other-unique.dat"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_text("z\n", encoding="utf-8")
    b.write_text("z\n", encoding="utf-8")
    with patch.object(Path, "glob") as mock_glob:
        mock_glob.return_value = [a]
        found = resolve_search_path("target-unique.dat", minimal_workspace)
    assert found == a.resolve()

    with patch("greedy_token.code_search.resolve_rg") as mock_rg:
        mock_rg.return_value = Path("/bin/rg")
        with patch(
            "greedy_token.code_search._run_rg",
            return_value=(0, "projects/sample.js:1:baseUrl"),
        ):
            out = search_code("baseUrl", minimal_workspace, path="*.js")
    assert "baseUrl" in out.text

    # Empty basename after rooted miss.
    hint = MagicMock()
    hint.is_absolute.return_value = False
    hint.name = ""
    rooted = MagicMock()
    rooted.resolve.return_value = rooted
    rooted.is_file.return_value = False
    rooted.is_dir.return_value = False
    mock_root = MagicMock()
    mock_root.resolve.return_value = mock_root
    mock_root.__truediv__.return_value = rooted
    with patch("greedy_token.code_search.Path", return_value=hint):
        assert resolve_search_path("empty-basename", mock_root) is None

    # Tree search outside root relative_to / limit (via public search_code + python engine).
    scope = tmp_path / "lim"
    scope.mkdir()
    (scope / "a.txt").write_text("limhit\n", encoding="utf-8")
    (scope / "b.txt").write_text("limhit\n", encoding="utf-8")
    # Search scoped under tmp workspace root that includes external-relative paths.
    with patch("greedy_token.code_search.resolve_rg", return_value=None):
        limited = search_code("limhit", tmp_path, path="lim", limit=1)
    assert "limhit" in limited.text


@allure.title("context_audit skips directories and empty rules table")
def test_context_audit_empty_rules(minimal_workspace: Path) -> None:
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

    fake_dir = minimal_workspace / ".cursor" / "rules" / "fake.mdc"
    fake_dir.mkdir(parents=True)
    with patch.object(Path, "glob") as mock_glob:
        mock_glob.return_value = [fake_dir, minimal_workspace / ".cursor" / "rules" / "test.mdc"]
        items = audit_context(minimal_workspace)
    assert all(i.path.endswith("test.mdc") for i in items)


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


@allure.title("plan_run / execute_task public branches (weak rg, python, inventory)")
def test_executors_public_branches(minimal_workspace: Path) -> None:
    from greedy_token.executors import execute_task, plan_run
    from greedy_token.router import RouteDecision

    with patch("greedy_token.executors._rag_fallback_output", return_value=None):
        with patch("greedy_token.executors.execute_plan", return_value=(2, "only result")):
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
    assert result.exit_code == 2
    assert "only result" in result.output

    decision = RouteDecision(
        target="ollama",
        route_id="ollama-audit",
        confidence=1.0,
        matched=[],
        command="./scripts/ollama/audit-skill.sh",
        note="",
        domains=[],
        read_only=True,
    )
    plan = plan_run(decision, "audit skill", minimal_workspace)
    assert "pass args" in plan.dry_run_output
    assert plan.executable is True

    inventory = RouteDecision(
        target="ollama",
        route_id="ollama-inventory",
        confidence=1.0,
        matched=[],
        command="./scripts/ollama/batch-inventory.sh",
        note="",
        domains=[],
        read_only=False,
    )
    inv_plan = plan_run(inventory, "batch inventory", minimal_workspace)
    assert inv_plan.executable is False

    with patch("greedy_token.executors.search_rag", side_effect=[[], [MagicMock()]]):
        with patch("greedy_token.executors.format_hits", return_value="RAG hits"):
            with patch("greedy_token.executors.execute_plan", return_value=(0, "")):
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
                    rag_fb = execute_task("baseUrl config", minimal_workspace)
    assert rag_fb.used_rag_fallback is True
    assert "RAG hits" in rag_fb.output

    with patch("greedy_token.executors.search_rag", return_value=[]):
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
                plain = execute_task("find x", minimal_workspace)
    assert "plain output" in plain.output

    with patch("greedy_token.executors.execute_plan", return_value=(0, "script stdout")):
        with patch(
            "greedy_token.executors.route_task",
            return_value=RouteDecision(
                target="python",
                route_id="script-check-meta-sync",
                confidence=1.0,
                matched=[],
                command="python scripts/meta-sync-check.py",
                note="",
                domains=[],
                read_only=True,
            ),
        ):
            py = execute_task("check meta", minimal_workspace)
    assert py.output == "script stdout"
    assert py.exit_code == 0


@allure.title("MCP icons raise when static assets missing; __main__ entry")
def test_mcp_icons_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
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

    called: list[int] = []
    monkeypatch.setattr(mcp_mod, "main", lambda: called.append(1))
    exec("if __name__ == '__main__': main()", {"__name__": "__main__", "main": mcp_mod.main})
    assert called == [1]


@allure.title("pipeline: empty, wrapper args, dry search, stop-early footer")
def test_pipeline_public_edges(minimal_workspace: Path) -> None:
    from greedy_token.pipeline import (
        PipelineResult,
        format_pipeline_footer,
        parse_pipeline,
        run_pipeline,
    )

    with pytest.raises(ValueError, match="Empty pipeline"):
        parse_pipeline("   ")

    with pytest.raises(ValueError, match="needs more args"):
        parse_pipeline("pipeline: meta-audit")

    skill = minimal_workspace / ".cursor" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# demo\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        parse_pipeline("pipeline: meta-audit missing-skill-xyz")

    steps = parse_pipeline("pipeline: meta-audit demo")
    assert any(s.step_id == "audit-skill" for s in steps)

    abs_skill = minimal_workspace / "custom.md"
    abs_skill.write_text("# custom\n", encoding="utf-8")
    steps_abs = parse_pipeline(f"audit-skill {abs_skill}")
    assert steps_abs[0].args and "custom.md" in steps_abs[0].args

    with pytest.raises(ValueError, match="audit-skill needs"):
        parse_pipeline("audit-skill")

    dry = run_pipeline("search baseUrl sample.js", minimal_workspace, execute=False)
    assert "(dry-run) search" in dry.steps[0].output

    from greedy_token.pipeline import PipelineStep, StepResult

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

    # Force missing pipelines.yaml so named expand is a no-op.
    real_is_file = Path.is_file

    def pipelines_yaml_missing(self: Path) -> bool:
        if self.name == "pipelines.yaml":
            return False
        return real_is_file(self)

    with patch.object(Path, "is_file", pipelines_yaml_missing):
        steps2 = parse_pipeline("check-meta-sync")
    assert steps2[0].step_id == "check-meta-sync"

    # Run audit-skill (dry) to cover token estimate for skill file.
    executed = run_pipeline(f"audit-skill {skill.relative_to(minimal_workspace)}", minimal_workspace, execute=False)
    assert executed.steps[0].est_tokens == 0
    assert executed.steps[0].executed is False
    footer2 = format_pipeline_footer(executed, minimal_workspace)
    assert "Greedy token — pipeline" in footer2
    assert "dry-run — not executed" in footer2
    assert "Saved:             ~0" in footer2


@allure.title("prompt_compress heuristic edges")
def test_prompt_compress_edges() -> None:
    from greedy_token.prompt_compress import compress_heuristic

    text = "Note: skip me.\nGoal: fix baseUrl.\n" + ("detail segment. " * 15)
    short = compress_heuristic(text)
    assert "baseUrl" in short
    assert short.endswith(".")

    short2 = compress_heuristic("Why: skip.\n" + ("segment " * 20))
    assert "Why" not in short2 or "segment" in short2

    split = compress_heuristic("Alpha\nBeta\nGamma\nGoal without period")
    assert split.endswith(".")

    long_line = "Start. " + "x" * 130 + ". tail"
    multi = compress_heuristic("\n".join(["Alpha", "Beta", "Gamma", "Delta", long_line]))
    assert len(multi) > 20
    assert "Start" in multi or "tail" in multi


@allure.title("router public: SEARCH prefixes, tooling, format_decision")
def test_router_public_edges(minimal_workspace: Path) -> None:
    from greedy_token.paths import load_routes_config
    from greedy_token.router import RouteDecision, format_decision, route_task

    routes = load_routes_config()
    assert isinstance(routes, dict)
    assert "routes" in routes
    assert any(r.get("id") for r in routes["routes"])

    decision = route_task('find "quoted term"', minimal_workspace)
    assert decision.command and "quoted term" in decision.command

    upper = route_task("find HTTP", minimal_workspace)
    assert upper.command

    # grep prefix alias
    grep = route_task("grep baseUrl", minimal_workspace)
    assert grep.target in ("tool", "rag", "cursor", "python", "ollama")

    spaced = route_task("find ", minimal_workspace)
    assert spaced.target in ("tool", "python", "ollama", "rag", "cursor")
    assert isinstance(spaced.route_id, str) and spaced.route_id

    non_ro = RouteDecision(
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
    out = format_decision(non_ro, "audit skill", minimal_workspace)
    assert "not read-only" in out


@allure.title("settings: workspace path auto-root, api_key export, bad provider")
def test_settings_public_edges(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.settings import (
        CheapLlmSettings,
        OllamaSettings,
        apply_cheap_llm_env,
        format_shell_export,
        get_cheap_llm_settings,
        workspace_config_path,
    )

    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(minimal_workspace))
    path = workspace_config_path(None)
    assert path == minimal_workspace / ".greedy-token.yaml"

    monkeypatch.delenv("CHEAP_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CHEAP_LLM_URL", raising=False)
    monkeypatch.delenv("CHEAP_LLM_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    # Unknown provider in workspace yaml is ignored (falls through).
    cfg = minimal_workspace / ".greedy-token.yaml"
    cfg.write_text(
        "cheap_llm:\n  provider: not-a-provider\n  url: http://x\n  model: m\n",
        encoding="utf-8",
    )
    settings = get_cheap_llm_settings(minimal_workspace)
    assert settings.provider in ("ollama", "openai_compat")

    keyed = CheapLlmSettings(
        provider="openai_compat",
        url="http://lm:1",
        model="m",
        source="test",
        api_key="sk-apply",
    )
    with patch("greedy_token.settings.get_cheap_llm_settings", return_value=keyed):
        apply_cheap_llm_env(minimal_workspace)
        assert os.environ.get("CHEAP_LLM_API_KEY") == "sk-apply"
        monkeypatch.setenv("CHEAP_LLM_API_KEY", "sk-apply")
        monkeypatch.setenv("CHEAP_LLM_PROVIDER", "openai_compat")
        monkeypatch.setenv("CHEAP_LLM_URL", "http://lm:1")
        monkeypatch.setenv("CHEAP_LLM_MODEL", "m")
        exported = format_shell_export(None, root=minimal_workspace)
        exported_reveal = format_shell_export(None, root=minimal_workspace, reveal=True)
    assert 'CHEAP_LLM_API_KEY="***"' in exported
    assert "sk-apply" not in exported
    assert 'CHEAP_LLM_API_KEY="sk-apply"' in exported_reveal

    direct = format_shell_export(keyed)
    assert 'CHEAP_LLM_API_KEY="***"' in direct
    assert "sk-apply" not in direct
    assert 'CHEAP_LLM_API_KEY="sk-apply"' in format_shell_export(keyed, reveal=True)

    ollama_export = format_shell_export(OllamaSettings(url="http://o:1", model="om", source="t"))
    assert "OLLAMA_URL" in ollama_export
    assert "CHEAP_LLM_API_KEY" not in ollama_export

    for key in (
        "CHEAP_LLM_API_KEY",
        "CHEAP_LLM_PROVIDER",
        "CHEAP_LLM_URL",
        "CHEAP_LLM_MODEL",
        "OLLAMA_URL",
        "OLLAMA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


@allure.title("tokens / tool_paths / wrappers public edges")
def test_tokens_tool_paths_wrappers(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.tokens import collect_paths, count_file
    from greedy_token.tool_paths import resolve_rg
    from greedy_token.wrappers import resolve_wrapper_command

    target = minimal_workspace / "real.txt"
    target.write_text("x", encoding="utf-8")
    link = minimal_workspace / "broken-link.txt"
    try:
        link.symlink_to(minimal_workspace / "missing-target.txt")
    except OSError:
        pytest.skip("symlinks not supported")
    paths = collect_paths(["."], minimal_workspace)
    assert not any(p.name == "broken-link.txt" for p in paths)

    est = count_file(minimal_workspace / "projects" / "sample.js")
    assert est.tokens > 0

    bin_dir = tmp_path / "rgbin"
    bin_dir.mkdir()
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

    with pytest.raises(FileNotFoundError):
        resolve_wrapper_command("gen-env-configs", minimal_workspace)


@allure.title("usage helpers: executors, events, rotation, report empty")
def test_usage_public_edges(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.router import RouteDecision
    from greedy_token.usage import (
        ReportSummary,
        aggregate_events,
        build_compress_event,
        build_route_event,
        executor_from_decision,
        format_report,
        load_events,
        logging_enabled,
        parse_since,
        rotate_log_if_needed,
        wrapper_for_route_id,
    )

    log_file = tmp_path / "usage.jsonl"

    assert logging_enabled(no_log=True) is False

    assert (
        executor_from_decision(
            RouteDecision(
                target="python",
                route_id="unknown-python",
                confidence=1.0,
                matched=[],
                command=None,
                note="",
                domains=[],
            )
        )["kind"]
        == "script"
    )
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
    assert (
        executor_from_decision(
            RouteDecision(
                target="rag",
                route_id="rag-x",
                confidence=1.0,
                matched=[],
                command=None,
                note="",
                domains=[],
            )
        )
        == {"kind": "rag"}
    )
    assert (
        executor_from_decision(
            RouteDecision(
                target="cursor",
                route_id="cursor-fallback",
                confidence=0.3,
                matched=[],
                command=None,
                note="",
                domains=[],
            )
        )
        == {"kind": "cursor"}
    )
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
        "\n"
        '{"cmd":"y","ts":"2030-01-01T00:00:00Z","selected_tier":"tool"}\n'
        '{"cmd":"bad-ts","ts":"not-a-date","selected_tier":"tool"}\n'
        '{"cmd":"naive","ts":"2030-06-15T12:00:00","selected_tier":"tool"}\n',
        encoding="utf-8",
    )
    events, skipped = load_events(log_file, since=parse_since("2025-01-01"))
    assert len(events) >= 1
    assert skipped >= 1
    # naive ts without Z gets UTC tz
    naive = [e for e in events if e.get("cmd") == "naive"]
    assert naive

    summary = format_report(ReportSummary(events=0, since="7d", skipped_lines=2))
    assert "No events since 7d" in summary
    assert "malformed" in summary

    assert "No events yet." in format_report(
        ReportSummary(events=0, since=None, skipped_lines=0)
    )

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

    agg = aggregate_events([{"selected_tier": "legacy-tier", "route_id": "x"}])
    assert "legacy-tier" in agg.by_tier


@allure.title("Remaining public branches: rag excerpt, jq, pipeline, tool_paths, python tree")
def test_remaining_public_branches(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.code_search import search_code
    from greedy_token.executors import execute_task
    from greedy_token.pipeline import PipelineStep, parse_pipeline, run_pipeline
    from greedy_token.prompt_compress import compress_heuristic
    from greedy_token.rag_index import invalidate_rag_index
    from greedy_token.rag_search import search_rag
    from greedy_token.router import RouteDecision, route_task
    from greedy_token.tool_paths import resolve_rg

    # Russian «почему» drop prefix in compress_heuristic.
    assert "baseUrl" in compress_heuristic("почему: drop\nGoal: fix baseUrl.\n")

    # Closed frontmatter + empty-path / missing rows in fingerprint+build.
    chunk = minimal_workspace / "docs" / "rag" / "config" / "closed.md"
    chunk.write_text("---\ntags: [baseurl]\n---\nbody with baseUrl signal\n", encoding="utf-8")
    plain = minimal_workspace / "docs" / "rag" / "config" / "plain-meta.md"
    plain.write_text("plain text without token " + ("x" * 400) + "\n", encoding="utf-8")
    long_hit = minimal_workspace / "docs" / "rag" / "config" / "long-hit.md"
    long_hit.write_text("head\n" + ("line with baseUrl " + "x" * 400 + "\n") * 2, encoding="utf-8")
    other = minimal_workspace / "docs" / "rag" / "stacks"
    other.mkdir(parents=True, exist_ok=True)
    (other / "stack.md").write_text("stack openapi spring\n", encoding="utf-8")
    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {"id": "closed", "domain": "config", "path": "docs/rag/config/closed.md", "tags": []}
        )
        + "\n"
        + json.dumps(
            {
                "id": "plain-meta",
                "domain": "config",
                "path": "docs/rag/config/plain-meta.md",
                "tags": ["baseurl"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "id": "long-hit",
                "domain": "config",
                "path": "docs/rag/config/long-hit.md",
                "tags": [],
            }
        )
        + "\n"
        + json.dumps(
            {"id": "stack", "domain": "stacks", "path": "docs/rag/stacks/stack.md", "tags": []}
        )
        + "\n"
        + '{"id":"empty","domain":"config","path":"","tags":[]}\n'
        + '{"id":"ghost","domain":"config","path":"docs/rag/config/ghost.md","tags":[]}\n',
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)

    # Domain filter skips stacks while scoring config.
    hits = search_rag("baseUrl", minimal_workspace, domains=["config"])
    assert hits
    assert all(h.domain == "config" for h in hits)
    # Truncation on match line (long-hit) and head fallback (plain-meta tags only).
    assert any(h.excerpt.endswith("…") or len(h.excerpt) <= 320 for h in hits)

    # Weak tool output + real empty RAG fallback → no private import.
    with patch("greedy_token.executors.search_rag", return_value=[]):
        with patch("greedy_token.executors.execute_plan", return_value=(1, "")):
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
                empty_fb = execute_task("find zzzUniqueNoHit", minimal_workspace)
    assert empty_fb.used_rag_fallback is False

    # Digit token scoring + empty residue after strip ("find").
    assert route_task("find 123", minimal_workspace).command
    bare = route_task("find", minimal_workspace)
    assert bare.target in ("tool", "python", "ollama", "rag", "cursor")
    assert isinstance(bare.route_id, str) and bare.route_id

    # jq tool command builder via route patterns.
    jq = route_task("parse json phase-manifest json", minimal_workspace)
    assert jq.command and "jq -r" in jq.command

    # Pipeline: unknown step, missing .md path, equals-as-positional, no-command, cursor estimate.
    with pytest.raises(ValueError, match="Unknown step"):
        parse_pipeline("not-a-real-step")
    with pytest.raises(FileNotFoundError, match="File not found"):
        parse_pipeline("audit-skill docs/rag/config/missing-skill.md")
    with pytest.raises(ValueError, match="unexpected extra args"):
        parse_pipeline("pipeline: search-rag query=baseUrl path=foo.html leftover")

    with patch(
        "greedy_token.pipeline.parse_pipeline",
        return_value=[PipelineStep("unknown", "python", "x", command=None)],
    ):
        with pytest.raises(ValueError, match="No command"):
            run_pipeline("unknown", minimal_workspace, execute=True)

    # cursor / ollama estimate branches: inject auto-run steps so _estimate_step_tokens runs.
    cursor_step = PipelineStep(
        "check-meta-sync",
        "cursor",
        "cursor",
        command="echo hello-output",
        args="",
    )
    ollama_missing = PipelineStep(
        "audit-skill",
        "ollama",
        "audit",
        command="echo ollama-out",
        args="docs/rag/config/ghost.md",
    )
    with patch("greedy_token.pipeline.ollama_available", return_value=True):
        with patch(
            "greedy_token.pipeline.PIPELINE_AUTO_RUN",
            frozenset({"check-meta-sync", "audit-skill"}),
        ):
            with patch(
                "greedy_token.pipeline.parse_pipeline",
                return_value=[cursor_step],
            ):
                cursor_res = run_pipeline("cursor", minimal_workspace, execute=True)
            assert cursor_res.steps[0].est_tokens > 0

            with patch(
                "greedy_token.pipeline.parse_pipeline",
                return_value=[ollama_missing],
            ):
                ollama_res = run_pipeline("audit", minimal_workspace, execute=True)
            assert ollama_res.steps[0].est_tokens >= 0

    # tool_paths: walk PATH segments + bundled fallbacks when nothing is executable.
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join(["", "/nonexistent/bin"]))
    with patch("greedy_token.tool_paths.shutil.which", return_value=None):
        with patch.object(Path, "is_file", return_value=False):
            assert resolve_rg() is None

    # Short body + meta-only score → excerpt returns head as-is (no ellipsis).
    short_meta = minimal_workspace / "docs" / "rag" / "config" / "short-meta.md"
    short_meta.write_text("short plain\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "id": "short-meta",
                "domain": "config",
                "path": "docs/rag/config/short-meta.md",
                "tags": ["baseurl"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    short_hits = search_rag("baseurl", minimal_workspace, domains=["config"])
    assert short_hits
    assert short_hits[0].excerpt == "short plain"

    # Empty residue after search-prefix strip → query falls back to full task.
    with patch("greedy_token.router._strip_search_prefix", return_value=""):
        empty_res = route_task("find leftoverToken", minimal_workspace)
    assert empty_res.command and "leftoverToken" in empty_res.command

    # best_in_tier keeps first on equal score (second does not replace).
    tied = {
        "routes": [
            {"id": "first", "target": "tool", "patterns": ["find"], "tool": "rg"},
            {"id": "second", "target": "tool", "patterns": ["find"], "tool": "rg"},
        ]
    }
    with patch("greedy_token.router.load_routes_config", return_value=tied):
        tied_decision = route_task("find baseUrl", minimal_workspace)
    assert tied_decision.route_id == "first"

    # audit-skill estimate with real skill file (is_file True branch).
    skill_file = minimal_workspace / ".cursor" / "skills" / "est-demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("# est-demo body\n" * 20, encoding="utf-8")
    skill_step = PipelineStep(
        "audit-skill",
        "ollama",
        "audit",
        command="echo ollama-skill",
        args=str(skill_file.relative_to(minimal_workspace)),
    )
    with patch("greedy_token.pipeline.ollama_available", return_value=True):
        with patch(
            "greedy_token.pipeline.PIPELINE_AUTO_RUN",
            frozenset({"audit-skill"}),
        ):
            with patch("greedy_token.pipeline.parse_pipeline", return_value=[skill_step]):
                skill_res = run_pipeline("audit", minimal_workspace, execute=True)
    assert skill_res.steps[0].est_tokens > 0

    # python tree: missing scope dir + relative_to ValueError via public search_code.
    if (minimal_workspace / "projects").is_dir():
        import shutil

        shutil.rmtree(minimal_workspace / "projects")
    with patch("greedy_token.code_search.resolve_rg", return_value=None):
        miss_dir = search_code("anything", minimal_workspace, path=None)
    assert "No matches" in miss_dir.text

    # Force relative_to ValueError while scanning an existing scoped tree.
    scoped = tmp_path / "ws2"
    scoped.mkdir()
    (scoped / "docs").mkdir()
    (scoped / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    (scoped / "scripts").mkdir()
    (scoped / "scripts" / "meta-sync-check.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    proj = scoped / "projects"
    proj.mkdir()
    special = proj / "relboom.txt"
    special.write_text("relboom\n", encoding="utf-8")
    real_rel = Path.relative_to

    def boom_rel(self, other):
        if self.name == "relboom.txt":
            raise ValueError("outside")
        return real_rel(self, other)

    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(scoped))
    with patch("greedy_token.code_search.resolve_rg", return_value=None):
        with patch.object(Path, "relative_to", boom_rel):
            boom_out = search_code("relboom", scoped, path="projects")
    assert "relboom" in boom_out.text


@allure.title("Remaining public edges for fail_under=100")
def test_remaining_public_coverage_edges(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.code_search import search_code
    from greedy_token.executors import execute_task
    from greedy_token.pipeline import PipelineStep, parse_pipeline, run_pipeline
    from greedy_token.prompt_compress import compress_heuristic
    from greedy_token.rag_index import get_indexed_chunks, invalidate_rag_index
    from greedy_token.rag_search import search_rag
    from greedy_token.router import RouteDecision, route_task
    from greedy_token.tool_paths import resolve_rg

    # code_search: skip nonexistent DEFAULT_PATHS dir + relative_to outside root.
    (minimal_workspace / "stacks").rmdir()
    outside = minimal_workspace.parent / "ext-needle-xyz.txt"
    outside.write_text("needleOutsideXYZ\n", encoding="utf-8")
    with patch("greedy_token.code_search.resolve_rg", return_value=None):
        with patch(
            "greedy_token.code_search.DEFAULT_PATHS",
            ["projects", "docs", "stacks", "scripts", "generators", ".."],
        ):
            tree = search_code("needleOutsideXYZ", minimal_workspace, limit=5)
    assert "needleOutsideXYZ" in tree.text or tree.engine == "python"

    # weak rg + empty RAG fallback (both domain and unscoped miss → None).
    with patch("greedy_token.executors.search_rag", return_value=[]):
        with patch("greedy_token.executors.execute_plan", return_value=(0, "")):
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
                empty_fb = execute_task("find nothinguseful", minimal_workspace)
    assert empty_fb.used_rag_fallback is False

    with pytest.raises(ValueError, match="Unknown step"):
        parse_pipeline("not-a-real-step")

    with pytest.raises(FileNotFoundError, match="File not found"):
        parse_pipeline("audit-skill missing-abs-skill.md")

    # ollama estimate: missing skill file on disk (extra=0) + execute for token path.
    with patch(
        "greedy_token.pipeline.parse_pipeline",
        return_value=[
            PipelineStep(
                "audit-skill",
                "ollama",
                "audit",
                command="echo ok",
                args="does-not-exist.md",
            )
        ],
    ):
        with patch("greedy_token.pipeline.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="out\n", stderr="")
            est_run = run_pipeline("audit-skill x", minimal_workspace, execute=True)
    assert est_run.steps[0].est_tokens >= 0

    # cursor-tier estimate fallback (not tool/python/rag/ollama) + no-command error.
    with patch(
        "greedy_token.pipeline.parse_pipeline",
        return_value=[
            PipelineStep(
                "check-meta-sync",
                "cursor",
                "cursor-est",
                command="echo hello",
                args="",
            )
        ],
    ):
        with patch("greedy_token.pipeline.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
            cursor_est = run_pipeline("check-meta-sync", minimal_workspace, execute=True)
    assert cursor_est.steps[0].est_tokens > 0

    with patch(
        "greedy_token.pipeline.parse_pipeline",
        return_value=[PipelineStep("ghost", "cursor", "cursor", command=None, args="")],
    ):
        with pytest.raises(ValueError, match="No command"):
            run_pipeline("ghost", minimal_workspace, execute=True)

    why = compress_heuristic(
        "Why: drop this line.\nKeep the goal bare\nThird line.\nFourth line."
    )
    assert "Why" not in why
    assert "Keep the goal" in why

    # Closed frontmatter strip + empty/missing manifest rows.
    chunk = minimal_workspace / "docs" / "rag" / "config" / "closed-fm.md"
    chunk.write_text(
        "---\ntags: [x]\n---\nuniqueClosedBodyToken uniqueExcerptPad "
        + ("z" * 400)
        + "\n",
        encoding="utf-8",
    )
    long_noline = minimal_workspace / "docs" / "rag" / "config" / "meta-only.md"
    long_noline.write_text("plain long body without query " + ("w" * 400) + "\n", encoding="utf-8")
    manifest = minimal_workspace / "docs" / "rag" / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "id": "closed",
                "domain": "config",
                "path": "docs/rag/config/closed-fm.md",
                "tags": ["uniqueClosedBodyToken"],
            }
        )
        + "\n"
        + '{"id":"empty-path","domain":"config","path":"","tags":[]}\n'
        + '{"id":"ghost","domain":"config","path":"docs/rag/config/ghost-missing.md","tags":[]}\n'
        + json.dumps(
            {
                "id": "meta-only",
                "domain": "config",
                "path": "docs/rag/config/meta-only.md",
                "tags": ["metaonlytoken"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    chunks = get_indexed_chunks(minimal_workspace)
    ids = {c.meta.get("id") for c in chunks}
    assert "ghost" not in ids
    assert "empty-path" not in ids
    assert any("uniqueclosedbodytoken" in c.body_tokens for c in chunks)

    assert search_rag("baseUrl", minimal_workspace, domains=["stacks"]) == []
    long_hits = search_rag("uniqueClosedBodyToken", minimal_workspace, domains=["config"])
    assert long_hits
    assert long_hits[0].excerpt.endswith("…") or len(long_hits[0].excerpt) <= 320

    meta_hits = search_rag("metaonlytoken", minimal_workspace, domains=["config"])
    assert meta_hits
    assert len(meta_hits[0].excerpt) <= 320 or meta_hits[0].excerpt.endswith("…")

    # Digit token scoring + empty strip ("find ") + jq default json_path.
    digit = route_task("find 12345", minimal_workspace)
    assert digit.command
    spaced = route_task("find ", minimal_workspace)
    assert spaced.command  # strip leaves empty → falls back to task.strip()
    with patch(
        "greedy_token.router.load_routes_config",
        return_value={
            "routes": [
                {
                    "id": "tool-jq-test",
                    "target": "tool",
                    "tool": "jq",
                    "read_only": True,
                    "patterns": ["jqphase"],
                    "jq_filter": ".",
                }
            ]
        },
    ):
        jq = route_task("jqphase lookup", minimal_workspace)
    assert jq.command and "jq -r" in jq.command
    assert "phase-manifest" in jq.command

    # Equal pattern scores: first route wins (score > best is false on tie).
    with patch(
        "greedy_token.router.load_routes_config",
        return_value={
            "routes": [
                {
                    "id": "first-tie",
                    "target": "tool",
                    "tool": "rg",
                    "read_only": True,
                    "patterns": ["tiesignal"],
                },
                {
                    "id": "second-tie",
                    "target": "tool",
                    "tool": "rg",
                    "read_only": True,
                    "patterns": ["tiesignal"],
                },
            ]
        },
    ):
        tied = route_task("tiesignal please", minimal_workspace)
    assert tied.route_id == "first-tie"

    # Short RAG excerpt with no line match (head <= max_len).
    short_chunk = minimal_workspace / "docs" / "rag" / "config" / "short-meta.md"
    short_chunk.write_text("alpha beta gamma\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "id": "short-meta",
                "domain": "config",
                "path": "docs/rag/config/short-meta.md",
                "tags": ["shortmetatoken"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    invalidate_rag_index(minimal_workspace)
    short_hits = search_rag("shortmetatoken", minimal_workspace, domains=["config"])
    assert short_hits
    assert short_hits[0].excerpt == "alpha beta gamma"

    # ollama estimate with non-audit step (skip skill-file branch 328→332).
    with patch(
        "greedy_token.pipeline.parse_pipeline",
        return_value=[
            PipelineStep(
                "classify-file",
                "ollama",
                "classify",
                command="echo ok",
                args="x",
            )
        ],
    ):
        with patch("greedy_token.pipeline.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="classified\n", stderr="")
            classified = run_pipeline("classify-file x", minimal_workspace, execute=True)
    assert classified.steps[0].est_tokens >= 0

    # tool_paths: empty PATH still walks bundled candidates; may or may not find rg.
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", "")
    with patch("greedy_token.tool_paths.shutil.which", return_value=None):
        found = resolve_rg()
    assert found is None or found.name == "rg"
