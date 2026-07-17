"""Unit tests for pipeline step-runner edge branches (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

import allure
import pytest

import greedy_token.pipeline as pl
from greedy_token.pipeline import PipelineResult, PipelineStep, StepResult

pytestmark = [
    allure.epic("Pipeline"),
    allure.parent_suite("Pipeline"),
    allure.feature("Step runner"),
    allure.suite("Pipeline gaps"),
]


def _step(step_id: str, **kw) -> PipelineStep:
    base = dict(tier="tool", label=step_id, command=None, args="", profile="")
    base.update(kw)
    return PipelineStep(step_id=step_id, **base)


@allure.title("read-hits: dry-run, missing prior output, and unparseable hits")
def test_read_hits_early_returns(tmp_path: Path) -> None:
    step = _step("read-hits", tier="tool")

    dry = pl._run_read_hits(step, tmp_path, prior_search_output="x:1:y", execute=False)
    assert dry.ok and not dry.executed and "dry-run" in dry.output

    none = pl._run_read_hits(step, tmp_path, prior_search_output=None, execute=True)
    assert not none.ok and "no prior search" in none.output

    empty = pl._run_read_hits(step, tmp_path, prior_search_output="no hits here", execute=True)
    assert empty.ok and "no path:line hits" in empty.output


@allure.title("read-hits: enriches parsed hits honouring the file mode arg")
def test_read_hits_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    step = _step("read-hits", tier="tool", args="file")
    out = pl._run_read_hits(step, tmp_path, prior_search_output="sample.txt:2:beta", execute=True)
    assert out.ok and out.executed and "sample.txt" in out.output


@allure.title("rag step tolerates missing/empty prior search files")
def test_rag_prior_search(minimal_workspace: Path) -> None:
    step = _step("rag", tier="rag", args="baseUrl")

    no_prior = pl._run_step(step, minimal_workspace, execute=True, prior_search_output=None)
    assert no_prior.ok

    empty_files = pl._run_step(step, minimal_workspace, execute=True, prior_search_output="garbage line")
    assert empty_files.ok and "prior search files" not in empty_files.output


@allure.title("ollama step falls back to cheap-llm env when profile fails to resolve")
def test_ollama_profile_resolve_fallback(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.wrappers import WRAPPERS

    step_id = next(s for s in pl.PIPELINE_AUTO_RUN if s in WRAPPERS)
    step = _step(step_id, tier="ollama", command="echo hi", profile="fast")

    recorded: dict = {}
    monkeypatch.setattr(pl, "resolve_model", lambda *a, **k: (_ for _ in ()).throw(ValueError("no model")))
    monkeypatch.setattr(pl, "apply_cheap_llm_env", lambda root, **k: recorded.update(k))
    monkeypatch.setattr(pl, "ollama_available", lambda: False)

    out = pl._run_step(step, minimal_workspace, execute=True)
    assert not out.ok and "Cheap LLM unavailable" in out.output
    assert recorded.get("profile") == "fast"


@allure.title("footer executor summary names the active ollama model id")
def test_footer_ollama_model_id(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "qwen2.5-coder:7b")
    step = _step("classify-file", tier="ollama", label="classify")
    sr = StepResult(step=step, ok=True, exit_code=0, output="done", duration_ms=5, est_tokens=100, executed=True)
    result = PipelineResult(task="classify something", steps=[sr])
    out = pl.format_pipeline_footer(result, minimal_workspace)
    assert "qwen2.5-coder:7b" in out
