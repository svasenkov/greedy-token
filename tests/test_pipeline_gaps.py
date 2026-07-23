"""Unit tests for pipeline step-runner edge branches (fail_under=100)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import allure
import pytest

import greedy_token.pipeline as pl
from greedy_token.code_search import SearchResult
from greedy_token.pipeline import PipelineResult, PipelineStep, StepResult


def _fixed_time(monkeypatch: pytest.MonkeyPatch, t0: float = 5.0, t1: float = 7.0) -> None:
    """Pin perf_counter to a 2.0s delta so duration_ms is deterministically 2000.

    t0 is non-zero so the (end - t0) subtraction is observable (kills + / abs mutants).
    """
    monkeypatch.setattr(pl.time, "perf_counter", Mock(side_effect=[t0, t1]))

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
    # 2.0 s delta with a NON-ZERO t0 so duration_ms is deterministically 2000 and the
    # (end - t0) subtraction is observable (kills + / abs / *1000 mutants) across branches.
    monkeypatch.setattr(
        pl.time, "perf_counter", Mock(side_effect=[5.0, 7.0, 15.0, 17.0, 25.0, 27.0])
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


@allure.title("_run_step search: exact fields + search_code args (execute and dry-run)")
def test_run_step_search_fields(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    step = _step("search", tier="tool", args="myquery\tsub/file.py")

    calls: dict = {}

    def fake_search_code(query, root=None, *, path=None, limit=50, context=None):
        calls.update(query=query, root=root, path=path, context=context)
        return SearchResult(text="RESULT-TEXT", engine="rg")

    monkeypatch.setattr(pl, "search_code", fake_search_code)

    with allure.step("execute: search_code(query, root, path=path, context='none')"):
        _fixed_time(monkeypatch)
        sr = pl._run_step(step, minimal_workspace, execute=True)
        assert calls == {
            "query": "myquery",
            "root": minimal_workspace,
            "path": "sub/file.py",
            "context": "none",
        }
    with allure.step("execute: every StepResult field is exact"):
        assert sr.step is step
        assert sr.ok is True
        assert sr.exit_code == 0
        assert sr.output == "RESULT-TEXT"
        assert sr.duration_ms == 2000
        assert sr.est_tokens == 0
        assert sr.executed is True
        assert sr.engine == "rg"

    with allure.step("dry-run without path: exact output, not executed, empty engine"):
        step_np = _step("search", tier="tool", args="myquery\t")
        _fixed_time(monkeypatch)
        dry = pl._run_step(step_np, minimal_workspace, execute=False)
        assert dry.output == "(dry-run) search 'myquery'"
        assert dry.executed is False
        assert dry.engine == ""
        assert dry.est_tokens == 0
        assert dry.ok is True
        assert dry.exit_code == 0
        assert dry.duration_ms == 2000

    with allure.step("dry-run with path: appends ' in <path>'"):
        _fixed_time(monkeypatch)
        dry2 = pl._run_step(step, minimal_workspace, execute=False)
        assert dry2.output == "(dry-run) search 'myquery' in sub/file.py"


@allure.title("_run_step rag: search_rag/format_hits args, prior-file section, exact fields")
def test_run_step_rag_fields(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    step = _step("rag", tier="rag", args="baseUrl")

    rag_calls: dict = {}
    fmt_calls: dict = {}

    def fake_search_rag(query, root, limit):
        rag_calls.update(query=query, root=root, limit=limit)
        return ["hit-obj"]

    def fake_format_hits(query, hits):
        fmt_calls.update(query=query, hits=tuple(hits))
        return "FMT-OUTPUT"

    est_calls: dict = {}

    def fake_estimate(step_arg, output, root):
        est_calls.update(step=step_arg, output=output, root=root)
        return 0

    monkeypatch.setattr(pl, "search_rag", fake_search_rag)
    monkeypatch.setattr(pl, "format_hits", fake_format_hits)
    monkeypatch.setattr(pl, "_estimate_step_tokens", fake_estimate)

    # Six unique prior paths → limit=5 shows exactly five (kills default-3 and 6 mutants).
    prior = "\n".join(f"file{i}.py:1:content" for i in range(6))

    with allure.step("execute with prior search output"):
        _fixed_time(monkeypatch)
        sr = pl._run_step(step, minimal_workspace, execute=True, prior_search_output=prior)

    with allure.step("search_rag(step.args, root, limit=5) and format_hits(step.args, hits)"):
        assert rag_calls == {"query": "baseUrl", "root": minimal_workspace, "limit": 5}
        assert fmt_calls == {"query": "baseUrl", "hits": ("hit-obj",)}

    with allure.step("_estimate_step_tokens gets the real step, full output, and root (not None)"):
        assert est_calls["step"] is step
        assert est_calls["root"] == minimal_workspace  # kills root→None
        assert est_calls["output"] == sr.output  # kills output→None; exact full rag output

    with allure.step("prior-file section lists exactly five files joined by newlines"):
        assert "--- prior search files ---" in sr.output
        section = sr.output.split("--- prior search files ---\n", 1)[1]
        listed = section.splitlines()
        assert listed == [f"file{i}.py" for i in range(5)]
        assert sr.output.startswith("FMT-OUTPUT\n\n")

    with allure.step("exact StepResult fields"):
        assert sr.step is step
        assert sr.ok is True
        assert sr.exit_code == 0
        assert sr.executed is True
        assert sr.duration_ms == 2000

    with allure.step("no prior output → plain format_hits result, no prior section"):
        _fixed_time(monkeypatch)
        sr2 = pl._run_step(step, minimal_workspace, execute=True, prior_search_output=None)
        assert sr2.output == "FMT-OUTPUT"

    with allure.step("dry-run rag → exact planned output, not executed"):
        _fixed_time(monkeypatch)
        dry = pl._run_step(step, minimal_workspace, execute=False)
        assert dry.output == "(dry-run) rag 'baseUrl'"
        assert dry.executed is False
        assert dry.est_tokens == 0


@allure.title("_run_step ollama unavailable: exact message, fields, and cheap-LLM root threading")
def test_run_step_ollama_unavailable_fields(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    step = _step("classify-file", tier="ollama", label="classify", command="echo hi")

    seen: dict = {}

    def fake_settings(root):
        seen["root"] = root
        return SimpleNamespace(provider="prov", model="mod", url="http://x")

    monkeypatch.setattr(pl, "apply_cheap_llm_env", lambda root, **k: None)
    monkeypatch.setattr(pl, "get_cheap_llm_settings", fake_settings)
    monkeypatch.setattr(pl, "ollama_available", lambda: False)

    _fixed_time(monkeypatch)
    sr = pl._run_step(step, minimal_workspace, execute=True)

    with allure.step("exact unavailable message"):
        assert sr.output == (
            "Cheap LLM unavailable (prov, http://x). "
            "Start the runtime or use the expensive LLM path (agent chat)."
        )
    with allure.step("get_cheap_llm_settings received the real root, not None"):
        assert seen["root"] == minimal_workspace
    with allure.step("exact StepResult fields"):
        assert sr.step is step
        assert sr.ok is False
        assert sr.exit_code == 1
        assert sr.est_tokens == 0
        assert sr.executed is False
        assert sr.duration_ms == 2000


@allure.title("_run_step ollama profile: resolve_model/apply_cheap_llm_env receive profile+root")
def test_run_step_ollama_profile_wiring(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pl, "ollama_available", lambda: False)
    monkeypatch.setattr(pl, "get_cheap_llm_settings",
                        lambda root: __import__("types").SimpleNamespace(provider="p", model="m", url="u"))

    with allure.step("profile resolves → resolve_model(profile, root=root) + apply_model_env"):
        rm_calls: dict = {}
        am_calls: dict = {}
        monkeypatch.setattr(pl, "resolve_model",
                            lambda profile, *, root: rm_calls.update(profile=profile, root=root) or "MODEL")
        monkeypatch.setattr(pl, "apply_model_env", lambda m: am_calls.update(model=m))
        step = _step("classify-file", tier="ollama", command="echo", profile="fast")
        _fixed_time(monkeypatch)
        pl._run_step(step, minimal_workspace, execute=True)
        assert rm_calls == {"profile": "fast", "root": minimal_workspace}
        assert am_calls == {"model": "MODEL"}

    with allure.step("profile resolve fails → apply_cheap_llm_env(root, profile=profile)"):
        ace_calls: dict = {}

        def boom(profile, *, root):
            raise ValueError("no model")

        monkeypatch.setattr(pl, "resolve_model", boom)
        monkeypatch.setattr(pl, "apply_cheap_llm_env",
                            lambda root, **k: ace_calls.update(root=root, **k))
        step2 = _step("classify-file", tier="ollama", command="echo", profile="fast")
        _fixed_time(monkeypatch)
        pl._run_step(step2, minimal_workspace, execute=True)
        assert ace_calls == {"root": minimal_workspace, "profile": "fast"}

    with allure.step("no profile → apply_cheap_llm_env(root) with no profile kwarg"):
        ace_calls2: dict = {}
        monkeypatch.setattr(pl, "apply_cheap_llm_env",
                            lambda root, **k: ace_calls2.update(root=root, **k))
        step3 = _step("classify-file", tier="ollama", command="echo", profile="")
        _fixed_time(monkeypatch)
        pl._run_step(step3, minimal_workspace, execute=True)
        assert ace_calls2 == {"root": minimal_workspace}


@allure.title("_run_step subprocess success: cwd/timeout, stdout+stderr, exact fields")
def test_run_step_subprocess_success(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step = _step("check-meta-sync", tier="python", command="echo hi")

    run_calls: dict = {}
    est_calls: dict = {}

    def fake_run(cmd, **kw):
        run_calls.update(cmd=cmd, cwd=kw.get("cwd"), timeout=kw.get("timeout"))
        return Mock(stdout="OUT\n", stderr="ERR\n", returncode=0)

    monkeypatch.setattr(pl.subprocess, "run", fake_run)
    monkeypatch.setattr(
        pl, "_estimate_step_tokens",
        lambda step, output, root: est_calls.update(output=output, root=root) or 0,
    )

    _fixed_time(monkeypatch)
    sr = pl._run_step(step, minimal_workspace, execute=True)

    with allure.step("subprocess.run gets cwd=root and timeout=SCRIPT_TIMEOUT"):
        assert run_calls["cwd"] == minimal_workspace
        assert run_calls["timeout"] == pl.SCRIPT_TIMEOUT
        assert run_calls["cmd"] == "echo hi"
    with allure.step("output = (stdout + stderr).strip(); _estimate_step_tokens got that output+root"):
        assert sr.output == "OUT\nERR"
        assert est_calls == {"output": "OUT\nERR\n", "root": minimal_workspace}
    with allure.step("exact fields for exit 0"):
        assert sr.ok is True
        assert sr.exit_code == 0
        assert sr.executed is True
        assert sr.est_tokens == 0
        assert sr.duration_ms == 2000

    with allure.step("empty streams → empty output (kills stdout/stderr 'or' default mutants)"):
        monkeypatch.setattr(pl.subprocess, "run",
                            lambda cmd, **kw: Mock(stdout="", stderr="", returncode=0))
        _fixed_time(monkeypatch)
        sr_empty = pl._run_step(step, minimal_workspace, execute=True)
        assert sr_empty.output == ""

    with allure.step("nonzero exit → ok False, exit_code preserved"):
        monkeypatch.setattr(pl.subprocess, "run",
                            lambda cmd, **kw: Mock(stdout="X", stderr="", returncode=3))
        _fixed_time(monkeypatch)
        sr_fail = pl._run_step(step, minimal_workspace, execute=True)
        assert sr_fail.ok is False
        assert sr_fail.exit_code == 3


@allure.title("_run_step subprocess timeout: exit 124, exact message and fields")
def test_run_step_subprocess_timeout(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    step = _step("check-meta-sync", tier="python", command="sleep 999")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, pl.SCRIPT_TIMEOUT)

    monkeypatch.setattr(pl.subprocess, "run", boom)
    _fixed_time(monkeypatch)
    sr = pl._run_step(step, minimal_workspace, execute=True)
    with allure.step("exact timeout StepResult"):
        assert sr.ok is False
        assert sr.exit_code == 124
        assert sr.output == f"Step timed out after {pl.SCRIPT_TIMEOUT}s: sleep 999"
        assert sr.est_tokens == 0
        assert sr.executed is True
        assert sr.duration_ms == 2000


@allure.title("_run_step non-allowlisted step on execute: skipped with exact message/fields")
def test_run_step_not_allowlisted(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    step = _step("phase1-rsync", tier="python", command="./scripts/migrate/phase1-rsync.sh")
    _fixed_time(monkeypatch)
    sr = pl._run_step(step, minimal_workspace, execute=True)
    with allure.step("exact skip output and fields"):
        assert sr.step is step
        assert sr.output == (
            "(skipped) phase1-rsync not in pipeline auto-run allowlist.\n"
            "Command: ./scripts/migrate/phase1-rsync.sh"
        )
        assert sr.ok is False
        assert sr.exit_code == 1
        assert sr.est_tokens == 0
        assert sr.executed is False
        assert sr.duration_ms == 2000


@allure.title("_run_step missing command raises ValueError")
def test_run_step_missing_command(minimal_workspace: Path) -> None:
    step = _step("check-meta-sync", tier="python", command=None)
    with pytest.raises(ValueError, match="No command for step check-meta-sync"):
        pl._run_step(step, minimal_workspace, execute=False)


@allure.title("_run_step dry-run subprocess step → planned command, not executed")
def test_run_step_dry_run_command(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    step = _step("check-meta-sync", tier="python", command="echo hi")
    _fixed_time(monkeypatch)
    sr = pl._run_step(step, minimal_workspace, execute=False)
    with allure.step("dry-run emits the planned command and does not execute"):
        assert sr.output == "(dry-run) echo hi"
        assert sr.executed is False
        assert sr.ok is True
        assert sr.exit_code == 0
        assert sr.est_tokens == 0
        assert sr.duration_ms == 2000


@allure.title("footer executor summary names the active ollama model id")
def test_footer_ollama_model_id(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "qwen2.5-coder:7b")
    step = _step("classify-file", tier="ollama", label="classify")
    sr = StepResult(step=step, ok=True, exit_code=0, output="done", duration_ms=5, est_tokens=100, executed=True)
    result = PipelineResult(task="classify something", steps=[sr])
    out = pl.format_pipeline_footer(result, minimal_workspace)
    assert "qwen2.5-coder:7b" in out


def _footer_sr(step_id: str, tier: str, **kw) -> StepResult:
    base = dict(ok=True, exit_code=0, output="o", duration_ms=12, est_tokens=30, executed=True, engine="")
    base.update(kw)
    return StepResult(step=PipelineStep(step_id, tier, step_id), **base)


def _patch_footer_helpers(monkeypatch: pytest.MonkeyPatch, savings) -> dict:
    """Pin footer's delegated helpers so only footer-owned lines vary. Returns savings-kwargs sink."""
    from types import SimpleNamespace

    cap: dict = {}
    monkeypatch.setattr(
        pl, "cursor_baseline_breakdown",
        lambda r, t: SimpleNamespace(
            total=1000, rules=100, task=200, overhead=300, source="default-estimate"
        ),
    )
    monkeypatch.setattr(
        pl, "get_cheap_llm_settings",
        lambda r: SimpleNamespace(provider="prov", model="mod", url="http://x"),
    )
    monkeypatch.setattr(pl, "compute_step_savings", lambda r, root: [])
    if savings is not None:
        def spy(**k):
            cap.update(k)
            return savings
        monkeypatch.setattr(pl, "format_savings_lines", spy)
    return cap


@allure.title("format_pipeline_footer: exact full output for a mixed executed/dry pipeline")
def test_footer_exact_mixed(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    cap = _patch_footer_helpers(monkeypatch, ["SAV_A", "SAV_B"])
    # long id (>28) proves the [:28] slice; dry FAIL step proves mode/status branches.
    steps = [
        _footer_sr("a" * 30, "ollama"),
        _footer_sr("short", "tool", ok=False, executed=False, duration_ms=5, est_tokens=0),
    ]
    result = PipelineResult(task="mytask", steps=steps)
    footer = pl.format_pipeline_footer(result, minimal_workspace)

    expected = [
        "",
        "---",
        "Greedy token — pipeline",
        "",
        "",
        "",
        "Run log:",
        "  step                         tier         ms   tokens  status",
        "  aaaaaaaaaaaaaaaaaaaaaaaaaaaa ollama       12       30 OK",
        "  short                        tool          5        0 FAIL (dry)",
        "",
        "Spent by executor:",
        "  rg (disk search) (0 LLM spend)   steps=1  ~0 tok",
        "  ollama (cheap LLM) (prov/mod, cheap) steps=1  ~30 tok",
        "",
        "Pipeline total: 0.0 s · ~30 LLM tokens spent",
        "",
        "Combined naive agent chat (whole pipeline as one agent task)",
        "  Always-on rules: ~100  (measured)",
        "  Task prompt:     ~200  (measured)",
        "  Agent overhead:  ~300  (default-estimate)",
        "  Total (naive agent chat):  ~1,000",
        "",
        "SAV_A",
        "SAV_B",
        "",
        "Note: Per-step baseline assumes a separate agent chat per step (rules+overhead each time).",
        "Pipeline total baseline = one agent chat for the full pipeline task.",
        "Dry-run steps report saved=0 until execute=true / --execute.",
        "Agent wrapper (MCP + reply) still uses agent tokens beyond executor rows.",
    ]
    with allure.step("exact footer body, line by line"):
        assert footer.split("\n") == expected
    with allure.step("format_savings_lines gets clamped saved=970, the pipeline spent_note, and baseline source"):
        assert cap == {
            "baseline": 1000,
            "spent": 30,
            "saved": 970,
            "spent_note": "sum of pipeline steps",
            "source": "default-estimate",
        }


@allure.title("format_pipeline_footer threads breakdown.source into both savings headings")
def test_footer_threads_breakdown_source(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The footer must reuse the breakdown's source snapshot, not re-resolve the
    ambient config (here: default-estimate) inside the table helpers."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        pl, "cursor_baseline_breakdown",
        lambda r, t: SimpleNamespace(
            total=1000, rules=100, task=200, overhead=300, source="calibrated"
        ),
    )
    monkeypatch.setattr(
        pl, "get_cheap_llm_settings",
        lambda r: SimpleNamespace(provider="prov", model="mod", url="http://x"),
    )
    result = PipelineResult(
        task="t", steps=[_footer_sr("s1", "tool", est_tokens=0, executed=True)]
    )
    footer = pl.format_pipeline_footer(result, minimal_workspace)
    with allure.step("both savings headings carry the breakdown source, not the ambient one"):
        assert (
            "Per-step savings (if each step were a separate naive agent chat; "
            "baseline: calibrated):"
        ) in footer
        assert "Saved by executor (sum of per-step savings; baseline: calibrated):" in footer
        assert "baseline: default-estimate" not in footer


@allure.title("format_pipeline_footer: ollama executor names GREEDY_LLM_MODEL_ID when set")
def test_footer_exact_model_id(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_LLM_MODEL_ID", "qwen:7b")
    _patch_footer_helpers(monkeypatch, ["SAV"])
    result = PipelineResult(task="t", steps=[_footer_sr("s1", "ollama", est_tokens=30, duration_ms=1)])
    footer = pl.format_pipeline_footer(result, minimal_workspace)
    with allure.step("exact executor line uses <model_id>/<llm.model>"):
        assert "  ollama (cheap LLM) (qwen:7b/mod, cheap) steps=1  ~30 tok" in footer.split("\n")


@allure.title("format_pipeline_footer: same-tier steps accumulate; total duration uses /1000")
def test_footer_tier_accumulation_and_duration(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    _patch_footer_helpers(monkeypatch, ["SAV"])
    steps = [
        _footer_sr("s1", "ollama", est_tokens=30, duration_ms=60000),
        _footer_sr("s2", "ollama", est_tokens=20, duration_ms=40000),
    ]
    footer = pl.format_pipeline_footer(PipelineResult(task="t", steps=steps), minimal_workspace).split("\n")
    with allure.step("two ollama steps accumulate to steps=2 / ~50 tok (kills by_tier.get(None))"):
        assert "  ollama (cheap LLM) (prov/mod, cheap) steps=2  ~50 tok" in footer
    with allure.step("total duration divides ms by 1000 (kills /1001)"):
        assert "Pipeline total: 100.0 s · ~50 LLM tokens spent" in footer


@allure.title("format_pipeline_footer: pure dry-run block + stopped-early line, exact")
def test_footer_exact_pure_dry(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_LLM_MODEL_ID", raising=False)
    # No savings spy: pure-dry branch does not call format_savings_lines.
    _patch_footer_helpers(monkeypatch, None)
    result = PipelineResult(
        task="t", steps=[_footer_sr("s1", "tool", executed=False, duration_ms=0, est_tokens=0)],
        stopped_early=True,
    )
    footer = pl.format_pipeline_footer(result, minimal_workspace)
    expected = [
        "",
        "---",
        "Greedy token — pipeline",
        "",
        "",
        "",
        "Run log:",
        "  step                         tier         ms   tokens  status",
        "  s1                           tool          0        0 OK (dry)",
        "",
        "Spent by executor:",
        "  rg (disk search) (0 LLM spend)   steps=1  ~0 tok",
        "",
        "Pipeline total: 0.0 s · ~0 LLM tokens spent",
        "",
        "Combined naive agent chat (whole pipeline as one agent task)",
        "  Always-on rules: ~100  (measured)",
        "  Task prompt:     ~200  (measured)",
        "  Agent overhead:  ~300  (default-estimate)",
        "  Total (naive agent chat):  ~1,000",
        "",
        "Saved vs naive agent chat (baseline: default-estimate)",
        "  Baseline (naive agent chat):  ~1,000  (default-estimate)",
        "  Spent (MCP executor, LLM tokens): ~0  (dry-run — steps not executed)",
        "  Saved:             ~0  (dry-run; re-run with execute=true / --execute; baseline: default-estimate)",
        "",
        "Note: Per-step baseline assumes a separate agent chat per step (rules+overhead each time).",
        "Pipeline total baseline = one agent chat for the full pipeline task.",
        "Dry-run steps report saved=0 until execute=true / --execute.",
        "Agent wrapper (MCP + reply) still uses agent tokens beyond executor rows.",
        "Pipeline stopped early due to step failure.",
    ]
    with allure.step("exact footer with pure dry-run savings block + stopped-early line"):
        assert footer.split("\n") == expected


@allure.title("_run_read_hits enrich: exact args threading, mode resolution, header, block join")
def test_run_read_hits_enrich_exact(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    settings = SimpleNamespace(max_snippet_files=7, context_lines=3, max_context_tokens=999)
    seen_gs: dict = {}

    def fake_get_settings(root):
        seen_gs["root"] = root
        return settings

    monkeypatch.setattr(pl, "get_search_settings", fake_get_settings)
    monkeypatch.setattr(pl, "parse_hit_lines", lambda prior: ["h1", "h2"])
    enr: dict = {}

    def fake_enrich(root, hits, *, mode, max_files, context_lines, max_tokens):
        enr.update(root=root, hits=tuple(hits), mode=mode, max_files=max_files,
                   context_lines=context_lines, max_tokens=max_tokens)
        return ("BLOCK", 2, 55)

    monkeypatch.setattr(pl, "enrich_search_hits", fake_enrich)
    up: dict = {}
    monkeypatch.setattr(
        "greedy_token.code_search.unique_hit_paths",
        lambda hits, limit: up.update(limit=limit) or ["p/a.py", "p/b.py"],
    )

    def run(args: str):
        step = _step("read-hits", tier="tool", args=args)
        monkeypatch.setattr(pl.time, "perf_counter", Mock(side_effect=[5.0, 7.0]))
        return pl._run_read_hits(step, minimal_workspace, prior_search_output="x:1:y", execute=True)

    with allure.step("mode='file': every enrich arg threaded, root not None, limit=max_snippet_files"):
        sr = run("file")
        assert seen_gs["root"] == minimal_workspace  # kills get_search_settings(None)
        assert enr == {
            "root": minimal_workspace, "hits": ("h1", "h2"), "mode": "file",
            "max_files": 7, "context_lines": 3, "max_tokens": 999,
        }
        assert up["limit"] == 7  # kills unique_hit_paths(hits,) with no limit
        assert sr.duration_ms == 2000  # kills +t0 / /1000 / *1001
        assert sr.est_tokens == 55
        assert sr.output == (
            "read-hits: 2 file(s) · ~55 tokens\nfiles: p/a.py, p/b.py\n\nBLOCK"
        )  # kills header ', '.join and "if not block" inversion

    with allure.step("mode='none' honoured; kills 'none'/'file' tuple-member mutants"):
        assert run("none").step.args == "none"
        assert enr["mode"] == "none"

    with allure.step("unknown arg → default 'snippet' (kills default-None and lower/in mutants)"):
        run("bogus-mode")
        assert enr["mode"] == "snippet"

    with allure.step("empty enrich block → output is header only (kills block/not-block branch)"):
        monkeypatch.setattr(pl, "enrich_search_hits",
                            lambda root, hits, **k: ("", 0, 0))
        sr2 = run("file")
        assert sr2.output == "read-hits: 0 file(s) · ~0 tokens\nfiles: p/a.py, p/b.py"


@allure.title("_estimate_step_tokens: rag threads root; non-audit-skill args add no extra")
def test_estimate_step_tokens_gaps(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.tokens import count_tokens

    with allure.step("rag branch passes the real root to rag_est_tokens (kills root→None)"):
        seen: dict = {}
        monkeypatch.setattr(pl, "search_rag", lambda a, r, limit: ["h"])
        monkeypatch.setattr("greedy_token.budget.rag_est_tokens",
                            lambda hits, root: seen.update(root=root) or 42)
        step = PipelineStep("x", "rag", "l", args="q")
        est = pl._estimate_step_tokens(step, "out", minimal_workspace)
        assert seen["root"] == minimal_workspace
        assert est == 42 + count_tokens("q").tokens

    with allure.step("non-audit-skill ollama step never reads a file (kills 'and'→'or')"):
        # Create a file whose name equals the step args; original code must NOT read it
        # because step_id != 'audit-skill'. The 'or' mutant would read it and add tokens.
        (minimal_workspace / "decoy.md").write_text("x" * 5000, encoding="utf-8")
        step2 = PipelineStep("classify-file", "ollama", "l", args="decoy.md")
        est2 = pl._estimate_step_tokens(step2, "hello", minimal_workspace)
        assert est2 == count_tokens("hello").tokens

    with allure.step("audit-skill invalid-UTF8 file uses errors='replace' (kills errors mutants)"):
        bad = minimal_workspace / "bad.md"
        bad.write_bytes(b"ok \xff\xfe bytes")
        step3 = PipelineStep("audit-skill", "ollama", "l", args="bad.md")
        est3 = pl._estimate_step_tokens(step3, "out", minimal_workspace)
        expected = (
            count_tokens(bad.read_text(encoding="utf-8", errors="replace")).tokens
            + count_tokens("out").tokens
        )
        assert est3 == expected


@allure.title("_log_pipeline: skips dry steps; builds exact route event per executed step")
def test_log_pipeline_exact(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executed = StepResult(
        step=PipelineStep("check-meta-sync", "python", "meta", command="echo hi"),
        ok=True, exit_code=0, output="o", duration_ms=42, est_tokens=13, executed=True,
    )
    dry = StepResult(
        step=PipelineStep("audit-skill", "ollama", "aud", command="c"),
        ok=True, exit_code=0, output="o", duration_ms=1, est_tokens=99, executed=False,
    )
    # dry FIRST: `continue` must keep iterating to the executed step (kills continue→break).
    result = PipelineResult(task="mytask", steps=[dry, executed])

    events: list = []
    calls: list[dict] = []

    def fake_build(**kw):
        calls.append(kw)
        return {"kw": kw}

    monkeypatch.setattr(pl, "build_route_event", fake_build)
    monkeypatch.setattr(pl, "append_event", lambda ev: events.append(ev))

    pl._log_pipeline(result, minimal_workspace)

    with allure.step("only the executed step is logged (kills continue→break and filter flip)"):
        assert len(calls) == 1
        assert len(events) == 1
    c = calls[0]
    with allure.step("build_route_event top-level kwargs are exact"):
        assert c["cmd"] == "pipeline"
        assert c["task"] == "mytask :: meta"
        assert c["root"] == minimal_workspace
        assert c["duration_ms"] == 42
        assert c["executed"] is True
        assert c["est_tokens_override"] == 13
        assert c["tier_scan"] == []
    with allure.step("the RouteDecision carries the exact per-step fields"):
        d = c["decision"]
        assert d.target == "python"
        assert d.route_id == "pipeline-check-meta-sync"
        assert d.confidence == 1.0
        assert d.matched == []
        assert d.command == "echo hi"
        assert d.note == ""
        assert d.domains == []
        assert d.est_tokens == 13


@allure.title("list_pipelines: exact full listing (desc present/absent, steps join, headers)")
def test_list_pipelines_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        "pipelines": {
            "p1": {"description": "Desc one", "steps": ["a", "b"]},
            "p2": {"steps": ["c"]},
        }
    }
    monkeypatch.setattr(pl, "_load_pipelines_config", lambda: cfg)
    out = pl.list_pipelines()
    expected = "\n".join(
        [
            "Named pipelines:",
            "",
            "  p1",
            "    Desc one",
            "    steps: a → b",
            "    usage: pipeline: p1 <args>",
            "",
            "  p2",
            "    steps: c",
            "    usage: pipeline: p2 <args>",
            "",
            "Custom chain:",
            "  pipeline: check-meta-sync then audit-skill configurator-boolean",
        ]
    )
    assert out == expected


@allure.title("_parse_segment: read-hits/wrapper branches yield exact PipelineStep fields")
def test_parse_segment_fields(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.wrappers import WRAPPERS

    with allure.step("read-hits branch: exact tier/label/args/profile"):
        rh = pl._parse_segment("read-hits some args", profile="P")
        assert rh == PipelineStep(
            step_id="read-hits", tier="tool", label="read-hits",
            command=None, args="some args", profile="P",
        )

    with allure.step("search branch threads the profile; profile default is ''"):
        assert pl._parse_segment("search q path=p", profile="P").profile == "P"  # kills profile=None/removed
        assert pl._parse_segment("read-hits x").profile == ""  # kills profile default "XXXX"

    monkeypatch.setattr(pl, "find_workspace_root", lambda: minimal_workspace)
    cmd_calls: dict = {}

    def fake_cmd(step_id, root, *, extra_args):
        cmd_calls.update(step_id=step_id, root=root, extra_args=extra_args)
        return "CMD"

    monkeypatch.setattr(pl, "resolve_wrapper_command", fake_cmd)

    with allure.step("python wrapper: split(maxsplit=1) keeps args; profile forced ''"):
        step = pl._parse_segment("check-meta-sync extra args", profile="P")
        assert step.step_id == "check-meta-sync"
        assert step.tier == "python"
        assert step.args == "extra args"  # kills rsplit / maxsplit=2
        assert step.label == "check-meta-sync extra args"
        assert step.command == "CMD"
        assert step.profile == ""  # kills profile→None / "XXXX" / requires_ollama flip
        assert cmd_calls == {
            "step_id": "check-meta-sync", "root": minimal_workspace, "extra_args": "extra args",
        }

    with allure.step("ollama wrapper: profile threaded through"):
        step2 = pl._parse_segment("batch-inventory foo", profile="P")
        assert step2.tier == "ollama"
        assert step2.profile == "P"

    with allure.step("unknown step raises the exact Known-list message"):
        with pytest.raises(ValueError) as ei:
            pl._parse_segment("nope-nope")
        assert str(ei.value) == (
            f"Unknown step 'nope-nope'. Known: {', '.join(sorted(WRAPPERS))}, "
            f"search, read-hits, rag"
        )


@allure.title("_parse_search_segment: partition (not rpartition), profile, label suffix")
def test_parse_search_segment_fields() -> None:
    with allure.step("first ' path=' splits query/path (partition, not rpartition)"):
        s = pl._parse_search_segment("search alpha path=one path=two", profile="P")
        assert s.step_id == "search"
        assert s.tier == "tool"
        assert s.args == "alpha\tone path=two"  # kills rpartition
        assert s.label == "search: alpha in one path=two"
        assert s.profile == "P"  # kills profile→None
    with allure.step("no path → label has no ' in ' suffix; profile default is ''"):
        s2 = pl._parse_search_segment("search justquery", profile="P")
        assert s2.args == "justquery\t"
        assert s2.label == "search: justquery"  # kills else "" → "XXXX"
        assert pl._parse_search_segment("search q").profile == ""  # kills profile default "XXXX"


@allure.title("run_pipeline: threads root/profile, prior-search output, and logs on execute")
def test_run_pipeline_wiring(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pp: dict = {}

    def fake_parse(task, *, profile):
        pp.update(task=task, profile=profile)
        return [
            PipelineStep("check-meta-sync", "python", "l", command="c"),
            PipelineStep("read-hits", "tool", "l"),
        ]

    monkeypatch.setattr(pl, "parse_pipeline", fake_parse)
    monkeypatch.setattr(pl, "find_workspace_root", lambda: Path("/should/not/be/used"))
    log: dict = {}
    monkeypatch.setattr(pl, "_log_pipeline", lambda result, root: log.update(root=root))
    rs: list = []

    def fake_run_step(step, root, *, execute, prior_search_output=None):
        rs.append((step.step_id, root, prior_search_output))
        return StepResult(step=step, ok=True, exit_code=0, output="OUT",
                          duration_ms=1, est_tokens=0, executed=True)

    monkeypatch.setattr(pl, "_run_step", fake_run_step)

    with allure.step("non-search executed step does NOT become prior_search_output"):
        pl.run_pipeline("mytask", minimal_workspace, execute=True)
        assert pp == {"task": "mytask", "profile": ""}  # default profile "" threaded
        assert rs[0][1] == minimal_workspace  # passed root used, not find_workspace_root()
        assert rs[1][2] is None  # kills last_search_output ""→None and search-and→or
        assert log["root"] == minimal_workspace  # _log_pipeline gets real root

    with allure.step("search output IS threaded into the following step"):
        rs.clear()

        def fake_parse2(task, *, profile):
            return [
                PipelineStep("search", "tool", "l", args="q\t"),
                PipelineStep("read-hits", "tool", "l"),
            ]

        monkeypatch.setattr(pl, "parse_pipeline", fake_parse2)
        pl.run_pipeline("t", minimal_workspace, execute=True)
        assert rs[1][2] == "OUT"


@allure.title("run_pipeline: truncates step output above the default cap, keeping the boundary")
def test_run_pipeline_truncation(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pl, "parse_pipeline",
        lambda task, *, profile: [PipelineStep("check-meta-sync", "python", "l", command="c")],
    )

    def mk(n: int):
        def fake(step, root, *, execute, prior_search_output=None):
            return StepResult(step=step, ok=True, exit_code=0, output="A" * n,
                              duration_ms=1, est_tokens=0, executed=True)
        return fake

    with allure.step("len == default cap (4000): not truncated (kills > → >=)"):
        monkeypatch.setattr(pl, "_run_step", mk(4000))
        r = pl.run_pipeline("t", minimal_workspace, execute=False)
        assert r.steps[0].output == "A" * 4000

    with allure.step("len 4001: truncated to cap-40 + suffix (kills default 4001, -40/-41, suffix)"):
        monkeypatch.setattr(pl, "_run_step", mk(4001))
        r2 = pl.run_pipeline("t", minimal_workspace, execute=False)
        assert r2.steps[0].output == "A" * 3960 + "\n… (truncated)"


@allure.title("parse_pipeline: strips only the first 'pipeline:' colon; threads profile; empty error")
def test_parse_pipeline_gaps(minimal_workspace: Path) -> None:
    with allure.step("everything after the first ':' is kept (kills split/rsplit/maxsplit variants)"):
        steps = pl.parse_pipeline("pipeline:rag a:b", profile="P")
        assert len(steps) == 1
        assert steps[0].step_id == "rag"
        assert steps[0].args == "a:b"
        assert steps[0].profile == "P"  # active_profile threaded into _parse_segment
    with allure.step("empty pipeline raises the exact example message"):
        with pytest.raises(ValueError) as ei:
            pl.parse_pipeline("pipeline:   ")
        assert str(ei.value) == (
            "Empty pipeline. Example: check-meta-sync then audit-skill configurator-boolean"
        )


@allure.title("parse_pipeline: active-profile threading between recipe and caller profile")
def test_parse_pipeline_profile_threading(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pl, "find_workspace_root", lambda: minimal_workspace)
    monkeypatch.setattr(pl, "resolve_wrapper_command", lambda step_id, root, *, extra_args: "CMD")

    with allure.step("named recipe without profile → caller profile is used"):
        monkeypatch.setattr(pl, "_load_pipelines_config",
                            lambda: {"pipelines": {"myp": {"steps": ["batch-inventory foo"]}}})
        steps = pl.parse_pipeline("myp", profile="P")
        assert steps[0].profile == "P"  # kills default_profile→None/removed

    with allure.step("named recipe with its own profile → recipe profile wins (kills active and-flip)"):
        monkeypatch.setattr(
            pl, "_load_pipelines_config",
            lambda: {"pipelines": {"myp": {"profile": "prof", "steps": ["batch-inventory foo"]}}},
        )
        steps2 = pl.parse_pipeline("myp", profile="P")
        assert steps2[0].profile == "prof"

    with allure.step("profile default '' when omitted (kills parse_pipeline profile default 'XXXX')"):
        monkeypatch.setattr(pl, "_load_pipelines_config", lambda: {"pipelines": {}})
        s3 = pl.parse_pipeline("batch-inventory foo")
        assert s3[0].profile == ""


@allure.title("_expand_named_pipeline: recipe profile, arg binding, step join, configurator special-case")
def test_expand_named_pipeline_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    with allure.step("recipe with profile: exact expansion + ' then ' join + recipe profile"):
        cfg = {"pipelines": {"myp": {"profile": "prof", "steps": ["check-meta-sync", "audit-skill {skill}"]}}}
        monkeypatch.setattr(pl, "_load_pipelines_config", lambda: cfg)
        text, prof = pl._expand_named_pipeline("myp otherskill", default_profile="def")
        assert text == "check-meta-sync then audit-skill otherskill"
        assert prof == "prof"

    with allure.step("recipe without profile: falls back to default_profile (kills default→None/name)"):
        cfg2 = {"pipelines": {"myp": {"steps": ["check-meta-sync"]}}}
        monkeypatch.setattr(pl, "_load_pipelines_config", lambda: cfg2)
        _, prof2 = pl._expand_named_pipeline("myp", default_profile="def")
        assert prof2 == "def"

    with allure.step("configurator-boolean special-case keeps running later steps (kills continue→break)"):
        cfg3 = {"pipelines": {"myp": {"profile": "p", "steps": ["audit-skill {skill}", "check-meta-sync"]}}}
        monkeypatch.setattr(pl, "_load_pipelines_config", lambda: cfg3)
        text3, _ = pl._expand_named_pipeline("myp configurator-boolean", default_profile="d")
        assert text3 == "configurator-boolean-audit then check-meta-sync"

    with allure.step("binding error names the recipe (kills _bind_recipe_args(None, …))"):
        cfg4 = {"pipelines": {"myp": {"steps": ["audit-skill {skill}"]}}}
        monkeypatch.setattr(pl, "_load_pipelines_config", lambda: cfg4)
        with pytest.raises(ValueError) as ei:
            pl._expand_named_pipeline("myp one two three", default_profile="d")
        assert "'myp'" in str(ei.value)

    with allure.step("default_profile defaults to '' (kills default → 'XXXX')"):
        monkeypatch.setattr(pl, "_load_pipelines_config", lambda: {"pipelines": {}})
        assert pl._expand_named_pipeline("rag q") == ("rag q", "")


@allure.title("_bind_recipe_args: usage string, extra-arg errors, query-join, path extraction")
def test_bind_recipe_args_gaps() -> None:
    B = pl._bind_recipe_args

    with allure.step("query placeholder joins multi-word positionals"):
        assert B("p", ["query"], ["hello", "world"], {}) == {"query": "hello world"}

    with allure.step("query+path: last positional is path, earlier tokens join into query"):
        assert B("p", ["query", "path"], ["a", "b", "c"], {}) == {"path": "c", "query": "a b"}

    with allure.step("single non-joinable placeholder binds one positional (kills 'in'→'not in')"):
        assert B("p", ["path"], ["x"], {}) == {"path": "x"}

    with allure.step("no placeholders but extra positionals → exact usage + space-joined extras"):
        with pytest.raises(ValueError) as e0:
            B("p", [], ["x", "y"], {})
        assert str(e0.value) == "Pipeline 'p' got unexpected extra args: 'x y'. Usage: pipeline: p <>"

    with allure.step("too few positionals → exact usage with '> <' join and '(or …=…)' hint"):
        with pytest.raises(ValueError) as e1:
            B("p", ["a", "b"], [], {})
        assert str(e1.value) == "Pipeline 'p' needs more args. Usage: pipeline: p <a> <b> (or b=…)"

    with allure.step("single placeholder, extra positionals → reports positional[1:] joined"):
        with pytest.raises(ValueError) as e2:
            B("p", ["a"], ["1", "2", "3"], {})
        assert str(e2.value) == (
            "Pipeline 'p' got unexpected extra args: '2 3'. Usage: pipeline: p <a> (or a=…)"
        )

    with allure.step("multi placeholder, extra positionals → space-joined positional[len(need):]"):
        with pytest.raises(ValueError) as e3:
            B("p", ["a", "b"], ["1", "2", "3", "4"], {})
        assert str(e3.value) == (
            "Pipeline 'p' got unexpected extra args: '3 4'. Usage: pipeline: p <a> <b> (or b=…)"
        )


@allure.title("_split_recipe_params: splits on the first '=' (kills rpartition)")
def test_split_recipe_params_gaps() -> None:
    positional, kwargs = pl._split_recipe_params(["query=x=y", "plain"], ["query"])
    assert kwargs == {"query": "x=y"}
    assert positional == ["plain"]


@allure.title("format_pipeline_response: threads the real root into body/footer")
def test_format_pipeline_response_root(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(pl, "format_pipeline_body", lambda r: "BODY")
    monkeypatch.setattr(pl, "format_pipeline_footer",
                        lambda r, root: seen.update(root=root) or "FOOT")
    monkeypatch.setattr(pl, "find_workspace_root", lambda: Path("/nope"))
    out = pl.format_pipeline_response(PipelineResult(task="t"), minimal_workspace)
    assert out == "BODYFOOT"
    assert seen["root"] == minimal_workspace  # kills root=None / root and find / footer(None)


@allure.title("format_pipeline_body: no stopped-early suffix when the run completed")
def test_format_pipeline_body_not_stopped() -> None:
    steps = [StepResult(step=PipelineStep("s", "tool", "lbl"), ok=True, exit_code=0,
                        output="", duration_ms=1, est_tokens=0, executed=True)]
    body = pl.format_pipeline_body(PipelineResult(task="t", steps=steps, stopped_early=False))
    assert body.split("\n")[1] == "Steps: 1"  # kills else "" → "XXXX"


@allure.title("compute_step_savings: row carries the step tier (kills tier→None)")
def test_compute_step_savings_tier(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pl, "cursor_baseline", lambda root, label: 100)
    steps = [StepResult(step=PipelineStep("s", "ollama", "lbl"), ok=True, exit_code=0,
                        output="", duration_ms=1, est_tokens=10, executed=True)]
    rows = pl.compute_step_savings(PipelineResult(task="t", steps=steps), minimal_workspace)
    assert rows[0].tier == "ollama"


@allure.title("_resolve_wrapper_args: exact empty-arg errors for classify-file and audit-skill")
def test_resolve_wrapper_args_errors(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pl, "find_workspace_root", lambda: minimal_workspace)
    with allure.step("classify-file with empty arg → exact message"):
        with pytest.raises(ValueError) as e1:
            pl._resolve_wrapper_args("classify-file", "  ")
        assert str(e1.value) == "classify-file needs a path under the workspace root"
    with allure.step("audit-skill with empty arg → exact message"):
        with pytest.raises(ValueError) as e2:
            pl._resolve_wrapper_args("audit-skill", "")
        assert str(e2.value) == "audit-skill needs skill name (e.g. configurator-boolean)"


@allure.title("_resolve_under_root: rejection message names the offending hint")
def test_resolve_under_root_hint(minimal_workspace: Path) -> None:
    with allure.step("absolute path outside root → hint appears in the error (kills hint=None)"):
        with pytest.raises(ValueError) as e1:
            pl._resolve_under_root("/etc/passwd", minimal_workspace)
        assert "/etc/passwd" in str(e1.value)
    with allure.step("relative escape outside root → hint appears in the error (kills hint=None)"):
        with pytest.raises(ValueError) as e2:
            pl._resolve_under_root("../../../../etc/passwd", minimal_workspace)
        assert "../../../../etc/passwd" in str(e2.value)
