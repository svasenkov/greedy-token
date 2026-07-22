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
def test_read_hits_early_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    step = _step("read-hits", tier="tool")
    # Each _run_read_hits call reads perf_counter exactly twice (t0 + end); pin a
    # 2.0 s delta so duration_ms is deterministically 2000 across every branch.
    monkeypatch.setattr(
        pl.time, "perf_counter", Mock(side_effect=[0.0, 2.0, 10.0, 12.0, 20.0, 22.0])
    )

    with allure.step("Dry-run branch: not executed, exit 0, exact output/engine"):
        dry = pl._run_read_hits(step, tmp_path, prior_search_output="x:1:y", execute=False)
        assert dry.step is step
        assert dry.ok is True
        assert dry.exit_code == 0
        assert dry.output == "(dry-run) read-hits from prior search"
        assert dry.est_tokens == 0
        assert dry.executed is False
        assert dry.engine == "read-hits"
        assert dry.duration_ms == 2000

    with allure.step("No prior output: failed, exit 1, executed"):
        none = pl._run_read_hits(step, tmp_path, prior_search_output=None, execute=True)
        assert none.step is step
        assert none.ok is False
        assert none.exit_code == 1
        assert none.output == "read-hits: no prior search step output to enrich"
        assert none.est_tokens == 0
        assert none.executed is True
        assert none.engine == "read-hits"
        assert none.duration_ms == 2000

    with allure.step("Unparseable prior output: ok, exit 0, executed, zero tokens"):
        empty = pl._run_read_hits(step, tmp_path, prior_search_output="no hits here", execute=True)
        assert empty.step is step
        assert empty.ok is True
        assert empty.exit_code == 0
        assert empty.output == "read-hits: no path:line hits parsed from prior search"
        assert empty.est_tokens == 0
        assert empty.executed is True
        assert empty.engine == "read-hits"
        assert empty.duration_ms == 2000


@allure.title("read-hits: enriches parsed hits honouring the file mode arg")
def test_read_hits_file_mode(tmp_path: Path) -> None:
    import re

    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    step = _step("read-hits", tier="tool", args="file")
    out = pl._run_read_hits(step, tmp_path, prior_search_output="sample.txt:2:beta", execute=True)
    with allure.step("Exact result fields for the enrich branch"):
        assert out.step is step
        assert out.ok is True
        assert out.exit_code == 0
        assert out.executed is True
        assert out.engine == "read-hits"
        assert out.output.startswith("read-hits: 1 file(s) · ~")
        assert "files: sample.txt" in out.output
    with allure.step("est_tokens matches the token count reported in the header"):
        m = re.search(r"~(\d+) tokens", out.output)
        assert m is not None
        assert out.est_tokens == int(m.group(1))


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
