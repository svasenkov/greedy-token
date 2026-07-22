from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import allure
import pytest

from greedy_token.pipeline import (
    PipelineResult,
    PipelineStep,
    StepResult,
    StepSavingsRow,
    compute_step_savings,
    format_executor_savings_summary,
    format_pipeline_footer,
    format_pipeline_step_savings_table,
    parse_pipeline,
    run_pipeline,
)
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Pipeline"),
    allure.parent_suite("Pipeline"),
    allure.feature("Multi-step chains"),
    allure.suite("Multi-step chains"),
]


@allure.story("Named recipes")
@allure.title("Parse meta-audit named pipeline recipe")
def test_parse_named_pipeline_meta_audit() -> None:
    with allure.step("Parse meta-audit pipeline recipe"):
        steps = parse_pipeline("pipeline: meta-audit configurator-boolean")
        attach_json("parsed steps", [{"step_id": s.step_id, "tier": s.tier, "args": s.args} for s in steps])
    with allure.step("Verify two-step meta-audit chain"):
        assert len(steps) == 2
        assert steps[0].step_id == "check-meta-sync"
        assert steps[0].tier == "python"
        assert steps[1].step_id == "configurator-boolean-audit"
        assert steps[1].tier == "python"


@allure.story("Named recipes")
@allure.title("Parse search-rag recipe and reuse query for RAG step")
def test_parse_named_pipeline_search_rag_reuses_query() -> None:
    with allure.step("Parse search-rag pipeline recipe"):
        steps = parse_pipeline("pipeline: search-rag baseUrl TestConfig")
        attach_json("parsed steps", [{"step_id": s.step_id, "tier": s.tier, "args": s.args} for s in steps])
    with allure.step("Verify search query is reused for RAG step"):
        assert len(steps) == 2
        assert steps[0].step_id == "search"
        assert steps[0].args == "baseUrl\tTestConfig"
        assert steps[1].step_id == "rag"
        assert steps[1].args == "baseUrl"


@allure.story("Named recipes")
@allure.title("Parse search-rag with path= keyword (agent / rule style)")
def test_parse_named_pipeline_search_rag_path_kwarg() -> None:
    with allure.step("Parse search-rag with path= keyword"):
        steps = parse_pipeline(
            "pipeline: search-rag baseUrl path=configurator-option-presets.html"
        )
        attach_json(
            "parsed steps",
            [{"step_id": s.step_id, "tier": s.tier, "args": s.args} for s in steps],
        )
    with allure.step("Verify path= is not treated as a literal path prefix"):
        assert len(steps) == 2
        assert steps[0].step_id == "search"
        assert steps[0].args == "baseUrl\tconfigurator-option-presets.html"
        assert steps[1].step_id == "rag"
        assert steps[1].args == "baseUrl"


@allure.story("Dry run")
@allure.title("run_pipeline defaults to dry-run (execute=False)")
def test_run_pipeline_default_is_dry_run(minimal_workspace: Path) -> None:
    with allure.step("Call run_pipeline without execute kwarg"):
        result = run_pipeline("check-meta-sync", minimal_workspace)
        attach_json(
            "step results",
            [{"executed": sr.executed, "ok": sr.ok} for sr in result.steps],
        )
    with allure.step("Verify default is dry-run"):
        assert len(result.steps) == 1
        assert result.steps[0].executed is False


@allure.story("Named recipes")
@allure.title("Excess recipe args raise ValueError")
def test_parse_named_pipeline_rejects_excess_args() -> None:
    with allure.step("Parse meta-audit with trailing junk"):
        with pytest.raises(ValueError, match="unexpected extra args"):
            parse_pipeline("pipeline: meta-audit configurator-boolean extra-tail")
    with allure.step("Parse search-rag with kwargs already bound + leftover"):
        with pytest.raises(ValueError, match="unexpected extra args"):
            parse_pipeline("pipeline: search-rag query=baseUrl path=foo.html leftover")


@allure.story("Named recipes")
@allure.title("Parse search-rag multi-word query with path= keyword")
def test_parse_named_pipeline_search_rag_multiword_query() -> None:
    with allure.step("Parse search-rag with multi-word query and path="):
        steps = parse_pipeline(
            "pipeline: search-rag hello world path=configurator-option-presets.html"
        )
        attach_json(
            "parsed steps",
            [{"step_id": s.step_id, "tier": s.tier, "args": s.args} for s in steps],
        )
    with allure.step("Verify query words join; path stays separate"):
        assert len(steps) == 2
        assert steps[0].step_id == "search"
        assert steps[0].args == "hello world\tconfigurator-option-presets.html"
        assert steps[1].step_id == "rag"
        assert steps[1].args == "hello world"


@allure.story("Named recipes")
@allure.title("Parse search-rag multi-word query with trailing path positional")
def test_parse_named_pipeline_search_rag_multiword_positional_path() -> None:
    with allure.step("Parse search-rag with multi-word query + path token"):
        steps = parse_pipeline("pipeline: search-rag MCP CLI route README.md")
    with allure.step("Verify last token is path; earlier tokens join as query"):
        assert steps[0].args == "MCP CLI route\tREADME.md"
        assert steps[1].args == "MCP CLI route"


@allure.story("Named recipes")
@allure.title("meta-rag joins multi-word query; unknown key= stays positional")
def test_parse_named_pipeline_meta_rag_and_unknown_equals() -> None:
    with allure.step("Parse meta-rag with multi-word query"):
        steps = parse_pipeline("pipeline: meta-rag hello world flag")
        assert steps[0].step_id == "check-meta-sync"
        assert steps[1].step_id == "rag"
        assert steps[1].args == "hello world flag"
    with allure.step("Unknown key=value is part of joined query; path= still binds"):
        steps2 = parse_pipeline(
            "pipeline: search-rag baseUrl foo=bar path=sample.js"
        )
        assert steps2[0].args == "baseUrl foo=bar\tsample.js"
        assert steps2[1].args == "baseUrl foo=bar"


@allure.story("Named recipes")
@allure.title("search-rag path= kwarg + multi-word query; missing args raise")
def test_parse_named_pipeline_search_rag_path_kwarg_and_missing() -> None:
    with allure.step("path= set; remaining positionals join as query"):
        steps = parse_pipeline(
            "pipeline: search-rag hello world path=sample.js"
        )
        assert steps[0].args == "hello world\tsample.js"
    with allure.step("path= alone without query raises"):
        with pytest.raises(ValueError, match="needs more args"):
            parse_pipeline("pipeline: search-rag path=sample.js")
    with allure.step("single-token recipe rejects multi positional"):
        with pytest.raises(ValueError, match="unexpected extra args"):
            parse_pipeline("pipeline: meta-audit skill-a skill-b")
    with allure.step("Direct bind covers empty known / zip fallback"):
        from greedy_token.pipeline import _bind_recipe_args

        assert _bind_recipe_args("x", [], [], {}) == {}
        with pytest.raises(ValueError, match="needs more args"):
            _bind_recipe_args("x", ["a", "b"], ["only"], {})
        bound = _bind_recipe_args("x", ["a", "b"], ["one", "two"], {})
        assert bound == {"a": "one", "b": "two"}
        with pytest.raises(ValueError, match="unexpected extra args"):
            _bind_recipe_args("x", ["a", "b"], ["one", "two", "three"], {})
        # path already in kwargs, but no query tokens left
        with pytest.raises(ValueError, match="needs more args"):
            _bind_recipe_args(
                "search-rag",
                ["query", "path"],
                [],
                {"path": "sample.js"},
            )
        # path + query needed but only one positional
        with pytest.raises(ValueError, match="needs more args"):
            _bind_recipe_args(
                "search-rag",
                ["query", "path"],
                ["only-query"],
                {},
            )
        # path= kwarg + multi-word query (path not in need_pos → joinable single slot)
        assert _bind_recipe_args(
            "search-rag",
            ["query", "path"],
            ["a", "b"],
            {"path": "p.html"},
        ) == {"path": "p.html", "query": "a b"}
        # three placeholders without path-join special case → zip
        assert _bind_recipe_args(
            "x",
            ["a", "b", "c"],
            ["1", "2", "3"],
            {},
        ) == {"a": "1", "b": "2", "c": "3"}
        # unknown kwarg key ignored in mapping loop
        assert _bind_recipe_args("x", ["skill"], ["ok"], {"nope": "x"}) == {
            "skill": "ok"
        }


@allure.story("Custom chain")
@allure.title("Parse custom then-chain pipeline syntax")
def test_parse_custom_chain() -> None:
    with allure.step("Parse custom then-chain pipeline"):
        steps = parse_pipeline("check-meta-sync then rag baseUrl -D flag")
        attach_json("parsed steps", [{"step_id": s.step_id, "tier": s.tier, "args": s.args} for s in steps])
    with allure.step("Verify check-meta-sync and rag steps"):
        assert steps[0].step_id == "check-meta-sync"
        assert steps[1].step_id == "rag"
        assert steps[1].args == "baseUrl -D flag"


@allure.story("Execute")
@allure.title("Pipeline execute runs search and RAG allowlisted steps")
def test_pipeline_execute_search_and_rag(minimal_workspace: Path) -> None:
    with allure.step("Execute search then RAG pipeline"):
        result = run_pipeline(
            "search baseUrl\tsample.js then rag baseUrl -D flag",
            minimal_workspace,
            execute=True,
        )
        attach_json("step results", [{"step_id": sr.step.step_id, "ok": sr.ok, "executed": sr.executed} for sr in result.steps])
        attach_text("search output", result.steps[0].output)
        attach_text("rag output", result.steps[1].output)
    with allure.step("Verify both steps executed successfully"):
        assert len(result.steps) == 2
        assert all(sr.executed for sr in result.steps)
        assert all(sr.ok for sr in result.steps)
        assert "baseUrl" in result.steps[0].output
        assert "RAG hits" in result.steps[1].output or "baseUrl" in result.steps[1].output


@allure.story("Execute")
@allure.title("Pipeline execute runs check-meta-sync wrapper script")
def test_pipeline_execute_check_meta_sync(minimal_workspace: Path) -> None:
    with allure.step("Execute check-meta-sync pipeline"):
        result = run_pipeline("check-meta-sync", minimal_workspace, execute=True)
        attach_text("step output", result.steps[0].output)
    with allure.step("Verify wrapper script succeeded"):
        assert len(result.steps) == 1
        step = result.steps[0]
        assert step.executed is True
        assert step.ok is True
        assert "meta-sync-check-ok" in step.output


@allure.story("Execute")
@allure.title("Pipeline execute runs meta-audit configurator-boolean via script tier")
def test_pipeline_execute_meta_audit_configurator_boolean_script(minimal_workspace: Path) -> None:
    with allure.step("Execute meta-audit pipeline for configurator-boolean"):
        result = run_pipeline(
            "meta-audit configurator-boolean",
            minimal_workspace,
            execute=True,
        )
        attach_json("step results", [{"step_id": sr.step.step_id, "ok": sr.ok, "executed": sr.executed} for sr in result.steps])
        attach_text("check-meta-sync output", result.steps[0].output)
        attach_text("configurator-boolean-audit output", result.steps[1].output)
    with allure.step("Verify both python steps completed without early stop"):
        assert len(result.steps) == 2
        assert not result.stopped_early
        assert result.steps[0].executed and result.steps[0].ok
        assert result.steps[1].executed and result.steps[1].ok
        assert result.steps[1].step.tier == "python"
        assert result.steps[1].step.step_id == "configurator-boolean-audit"
        assert "meta-sync-check-ok" in result.steps[0].output
        assert '"ok":true' in result.steps[1].output.replace(" ", "")


@allure.story("Execute")
@allure.title("Pipeline execute skips Ollama step when server is unavailable")
def test_pipeline_execute_skips_unavailable_ollama(minimal_workspace: Path) -> None:
    other_skill = minimal_workspace / ".cursor" / "skills" / "other-skill"
    other_skill.mkdir(parents=True, exist_ok=True)
    (other_skill / "SKILL.md").write_text("# other-skill\n", encoding="utf-8")
    with allure.step("Execute meta-audit with Ollama unavailable"):
        with patch("greedy_token.pipeline.ollama_available", return_value=False):
            result = run_pipeline(
                "meta-audit other-skill",
                minimal_workspace,
                execute=True,
            )
        attach_text("ollama step output", result.steps[1].output)
        attach_text("stopped early", str(result.stopped_early))
    with allure.step("Verify pipeline stops after Ollama failure"):
        assert result.stopped_early is True
        assert result.steps[0].ok is True
        assert result.steps[1].ok is False
        assert result.steps[1].step.step_id == "audit-skill"
        assert "Cheap LLM unavailable" in result.steps[1].output


@allure.story("Dry run")
@allure.title("Pipeline dry-run of an Ollama step never requires the runtime")
def test_pipeline_dry_run_ollama_no_runtime(minimal_workspace: Path) -> None:
    other_skill = minimal_workspace / ".cursor" / "skills" / "other-skill"
    other_skill.mkdir(parents=True, exist_ok=True)
    (other_skill / "SKILL.md").write_text("# other-skill\n", encoding="utf-8")
    with allure.step("Dry-run meta-audit with Ollama runtime down"):
        with patch(
            "greedy_token.pipeline.ollama_available", return_value=False
        ) as mock_avail:
            result = run_pipeline(
                "meta-audit other-skill",
                minimal_workspace,
                execute=False,
            )
        attach_text("ollama step output", result.steps[1].output)
    with allure.step("Verify the Ollama step is planned, not gated on availability"):
        assert result.stopped_early is False
        ollama_step = result.steps[1]
        assert ollama_step.step.tier == "ollama"
        assert ollama_step.ok is True
        assert ollama_step.executed is False
        assert "(dry-run)" in ollama_step.output
        assert "Cheap LLM unavailable" not in ollama_step.output
        mock_avail.assert_not_called()


@allure.story("Dry run")
@allure.title("Pipeline dry-run does not execute allowlisted steps")
def test_pipeline_dry_run(minimal_workspace: Path) -> None:
    with allure.step("Dry-run check-meta-sync then rag pipeline"):
        result = run_pipeline(
            "check-meta-sync then rag baseUrl",
            minimal_workspace,
            execute=False,
        )
        attach_json("planned steps", [{"step_id": sr.step.step_id, "tier": sr.step.tier, "executed": sr.executed} for sr in result.steps])
    with allure.step("Verify steps are planned but not executed"):
        assert len(result.steps) == 2
        assert result.steps[0].step.tier == "python"
        assert result.steps[1].step.tier == "rag"
        assert not result.steps[0].executed


@allure.story("Token footer")
@allure.title("Pipeline footer includes per-executor savings table")
def test_format_pipeline_footer_has_by_executor(minimal_workspace: Path) -> None:
    with allure.step("Format pipeline footer from dry-run result"):
        result = run_pipeline(
            "check-meta-sync then rag baseUrl",
            minimal_workspace,
            execute=False,
        )
        footer = format_pipeline_footer(result, minimal_workspace)
        attach_text("pipeline footer", footer)
        rows = compute_step_savings(result, minimal_workspace)
    with allure.step("Verify per-executor sections; dry-run does not inflate saved"):
        assert "Per-step savings" in footer
        assert "Saved by executor" in footer
        assert "Saved vs naive Cursor chat" in footer
        assert "dry-run — not executed" in footer
        assert "Saved:             ~0" in footer
        assert all(row.saved == 0 for row in rows)
        assert all(row.billing == "dry-run — not executed" for row in rows)


@allure.story("Token footer")
@allure.title("Pipeline search step billing uses executor_sub=rg (not tool→script)")
def test_pipeline_search_step_billing_uses_rg(minimal_workspace: Path) -> None:
    with allure.step("Execute search-rag and format footer"):
        result = run_pipeline(
            "pipeline: search-rag baseUrl sample.js",
            minimal_workspace,
            execute=True,
        )
        footer = format_pipeline_footer(result, minimal_workspace)
        attach_text("pipeline footer", footer)
    with allure.step("Verify search row shows rg + ripgrep billing"):
        assert any(
            sr.step.step_id == "search" and sr.executed for sr in result.steps
        )
        # executor column + billing hint (not "script — 0 LLM spend")
        assert "  search" in footer
        assert "rg" in footer
        assert "ripgrep on disk — 0 LLM spend" in footer
        assert "script — 0 LLM spend" not in footer.split("search")[1].split("\n")[0]


@allure.story("Token footer")
@allure.title("Pipeline search step billing uses python when rg unavailable")
def test_pipeline_search_step_billing_uses_python_fallback(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with allure.step("Disable ripgrep and execute search-rag"):
        monkeypatch.setattr("greedy_token.code_search.resolve_rg", lambda: None)
        result = run_pipeline(
            "pipeline: search-rag baseUrl sample.js",
            minimal_workspace,
            execute=True,
        )
        footer = format_pipeline_footer(result, minimal_workspace)
        attach_text("pipeline footer", footer)
    with allure.step("Verify search row shows python + script billing"):
        search_steps = [
            sr for sr in result.steps if sr.step.step_id == "search" and sr.executed
        ]
        assert search_steps and search_steps[0].engine == "python"
        rows = compute_step_savings(result, minimal_workspace)
        search_row = next(r for r in rows if r.step_id == "search")
        assert search_row.executor_sub == "python"
        assert "script — 0 LLM spend" in search_row.billing
        assert "ripgrep" not in search_row.billing
        assert "python" in footer
        assert "script — 0 LLM spend" in footer


@patch("greedy_token.pipeline._run_step")
@allure.story("Error handling")
@allure.title("Pipeline stops early when a step fails")
def test_pipeline_stops_on_error(mock_run, minimal_workspace: Path) -> None:
    from greedy_token.pipeline import PipelineStep, StepResult

    ok_step = StepResult(
        step=PipelineStep("check-meta-sync", "python", "meta", command="echo"),
        ok=True,
        exit_code=0,
        output="ok",
        duration_ms=1,
        est_tokens=0,
        executed=True,
    )
    fail_step = StepResult(
        step=PipelineStep("audit-skill", "ollama", "audit", command="echo"),
        ok=False,
        exit_code=1,
        output="fail",
        duration_ms=1,
        est_tokens=0,
        executed=True,
    )
    mock_run.side_effect = [ok_step, fail_step]
    with allure.step("Execute pipeline with mocked failing step"):
        result = run_pipeline("meta-audit configurator-boolean", minimal_workspace, execute=True)
        attach_json("step results", [{"ok": sr.ok, "executed": sr.executed} for sr in result.steps])
    with allure.step("Verify pipeline stops early on failure"):
        assert result.stopped_early
        assert len(result.steps) == 2


@allure.story("Pipeline result")
@allure.title("PipelineResult.all_ok is false for empty steps")
def test_pipeline_all_ok_empty() -> None:
    from greedy_token.pipeline import PipelineResult

    assert PipelineResult(task="empty").all_ok is False


@allure.story("Execute")
@allure.title("Pipeline skips non-allowlisted step on execute")
def test_pipeline_skips_non_allowlisted(minimal_workspace: Path) -> None:
    script = minimal_workspace / "scripts" / "migrate" / "phase1-rsync.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\necho rsync\n", encoding="utf-8")
    script.chmod(0o755)
    result = run_pipeline("phase1-rsync", minimal_workspace, execute=True)
    step = result.steps[0]
    assert step.executed is False
    assert "not in pipeline auto-run allowlist" in step.output


@patch("greedy_token.pipeline.subprocess.run")
@allure.story("Execute")
@allure.title("Pipeline step timeout returns exit 124")
def test_pipeline_step_timeout(mock_run, minimal_workspace: Path) -> None:
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired("cmd", 120)
    result = run_pipeline("check-meta-sync", minimal_workspace, execute=True)
    assert result.steps[0].exit_code == 124
    assert "timed out" in result.steps[0].output


def _row(**kw) -> StepSavingsRow:
    base = dict(
        index=1, step_id="check-meta-sync", tier="python", duration_ms=83,
        spent=0, baseline=9487, saved=9487, billing="script — 0 LLM spend",
        executor_sub="script",
    )
    base.update(kw)
    return StepSavingsRow(**base)


@allure.story("Savings table")
@allure.title("format_pipeline_step_savings_table emits exact header and row")
def test_format_savings_table_exact() -> None:
    assert format_pipeline_step_savings_table([]) == []
    lines = format_pipeline_step_savings_table([_row(index=1, duration_ms=83, spent=0, baseline=9487, saved=9487)])
    assert lines[0] == "Per-step savings (if each step were a separate naive Cursor chat):"
    # Exact header line pins column labels (kills case/text mutations).
    assert lines[1] == (
        f"  {'#':>2}  {'step':<22} {'executor':<8} {'ms':>6} "
        f"{'spent':>7} {'baseline':>9} {'saved':>9}  billing"
    )
    # Exact data row pins the format string and numeric grouping.
    assert lines[2] == (
        f"  {1:>2}  {'check-meta-sync':<22} {'script':<8} {83:>6} "
        f"{0:>7,} {9487:>9,} {9487:>9,}  script — 0 LLM spend"
    )


@allure.story("Savings table")
@allure.title("format_pipeline_step_savings_table falls back to tier when executor_sub empty")
def test_format_savings_table_executor_fallback() -> None:
    lines = format_pipeline_step_savings_table([_row(executor_sub="", tier="ollama")])
    assert " ollama   " in lines[2]


@allure.story("Executor summary")
@allure.title("format_executor_savings_summary aggregates per-tier with exact math")
def test_format_executor_summary_math() -> None:
    assert format_executor_savings_summary([]) == []
    rows = [
        _row(tier="python", spent=100, saved=200),
        _row(tier="python", spent=5, saved=6),
        _row(tier="tool", spent=1, saved=9),
    ]
    lines = format_executor_savings_summary(rows)
    # Iterates tiers in fixed order: tool before python.
    assert lines[0] == "Saved by executor (sum of per-step savings):"
    assert lines[1] == f"  {'rg (disk search)':<28} steps=1  spent ~1  saved ~9"
    # python: spent 100+5=105, saved 200+6=206, count=2 — kills tuple arithmetic mutants.
    assert lines[2] == f"  {'python (script)':<28} steps=2  spent ~105  saved ~206"


@allure.story("Compute savings")
@allure.title("compute_step_savings enumerates from 1, threads root, and clamps saved")
def test_compute_step_savings_math(minimal_workspace: Path) -> None:
    steps = [
        StepResult(
            step=PipelineStep("check-meta-sync", "python", "meta-label", command="c"),
            ok=True, exit_code=0, output="", duration_ms=1, est_tokens=100, executed=True,
        ),
        StepResult(
            step=PipelineStep("audit-skill", "ollama", "audit-label", command="c"),
            ok=True, exit_code=0, output="", duration_ms=1, est_tokens=50, executed=True,
        ),
        StepResult(
            step=PipelineStep("rag", "rag", "rag-label", command=None),
            ok=True, exit_code=0, output="", duration_ms=1, est_tokens=0, executed=False,
        ),
    ]
    result = PipelineResult(task="t", steps=steps)
    seen_roots: list[object] = []

    def fake_baseline(root, label):
        seen_roots.append(root)
        return {"meta-label": 1000, "audit-label": 50, "rag-label": 999}[label]

    with patch("greedy_token.pipeline.cursor_baseline", fake_baseline):
        rows = compute_step_savings(result, minimal_workspace)

    assert [r.index for r in rows] == [1, 2, 3]  # enumerate starts at 1
    assert all(r == minimal_workspace for r in seen_roots)  # root threaded, not None
    # baseline - spent, clamped at 0 (kills baseline+spent and max(1,...)).
    assert rows[0].saved == 900  # 1000 - 100
    assert rows[1].saved == 0  # max(0, 50 - 50)
    # dry-run step never claims savings.
    assert rows[2].saved == 0
    assert rows[2].billing == "dry-run — not executed"


@allure.story("Continue on error")
@allure.title("Pipeline continue-on-error keeps running after failure")
def test_pipeline_continue_on_error(minimal_workspace: Path) -> None:
    other_skill = minimal_workspace / ".cursor" / "skills" / "other-skill"
    other_skill.mkdir(parents=True, exist_ok=True)
    (other_skill / "SKILL.md").write_text("# other-skill\n", encoding="utf-8")
    with patch("greedy_token.pipeline.ollama_available", return_value=False):
        result = run_pipeline(
            "meta-audit other-skill",
            minimal_workspace,
            execute=True,
            stop_on_error=False,
        )
    assert len(result.steps) == 2
    assert result.steps[1].ok is False

