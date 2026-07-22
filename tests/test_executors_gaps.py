"""Mutation kill-tests for executors: exact fields on every return path.

Pins decision identity, exit codes, executable flags, root threading and the
dry-run / cursor / RAG strings so single-token mutants are caught with ``==``.
"""

from __future__ import annotations

from pathlib import Path

import allure
import pytest

from greedy_token import executors as ex
from greedy_token.executors import RunPlan, execute_plan, plan_run
from greedy_token.router import RouteDecision
from greedy_token.tool_paths import root_cd_prefix

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Task execution"),
    allure.suite("Executors gaps"),
]


def _dec(target: str, **kw) -> RouteDecision:
    base = dict(
        target=target, route_id="rid", confidence=1.0, matched=[], command=None,
        note="", domains=[], read_only=False,
    )
    base.update(kw)
    return RouteDecision(**base)


# --- plan_run: every branch, exact command / dry-run / executable / decision ---


@allure.title("plan_run tool tier: exact command, dry-run and executable")
def test_plan_run_tool_exact(minimal_workspace: Path) -> None:
    dec = _dec("tool", command="rg foo", read_only=True)
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.decision is dec
    assert plan.command == "rg foo"
    assert plan.dry_run_output == "rg foo"  # kills dry_run_output=None
    assert plan.executable is True


@allure.title("plan_run python tier: root-prefixed command threads real root + wrapper read_only")
def test_plan_run_python_wrapper_readonly(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # meta-sync-check.py maps to a read_only wrapper; decision.read_only is False,
    # so executability must come from the wrapper. A sentinel find_workspace_root
    # (distinct from the passed root) makes `root or ..` vs `root and ..` observable.
    monkeypatch.setattr(ex, "find_workspace_root", lambda: minimal_workspace / "SENTINEL")
    dec = _dec("python", command="python scripts/meta-sync-check.py", read_only=False)
    plan = plan_run(dec, "task", minimal_workspace)
    expected = f"{root_cd_prefix(minimal_workspace)} python scripts/meta-sync-check.py"
    assert plan.command == expected  # kills root=None / root and .. / root_cd_prefix(None)
    assert plan.dry_run_output == expected  # kills dry_run_output=None
    assert plan.executable is True  # kills wrapper=None / wrapper_for_command(None) / and
    assert plan.decision is dec


@allure.title("plan_run python tier: no wrapper + non-readonly decision → not executable")
def test_plan_run_python_no_wrapper(minimal_workspace: Path) -> None:
    dec = _dec("python", command="python scripts/no-such-wrapper-xyz.py", read_only=False)
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.executable is False  # kills `wrapper.read_only if wrapper else True`


@allure.title("plan_run ollama tier: exact command, hint suffix, wrapper read_only")
def test_plan_run_ollama_wrapper_readonly(minimal_workspace: Path) -> None:
    dec = _dec("ollama", command="./scripts/ollama/audit-skill.sh", read_only=False)
    plan = plan_run(dec, "task", minimal_workspace)
    expected = f"{root_cd_prefix(minimal_workspace)} ./scripts/ollama/audit-skill.sh"
    assert plan.command == expected  # kills root_cd_prefix(None)
    assert plan.dry_run_output == expected + "  # pass args as needed"  # kills 'XX' string
    assert plan.executable is True  # kills wrapper=None / wrapper_for_command(None) / and
    assert plan.decision is dec  # kills decision=None


@allure.title("plan_run ollama tier: no wrapper + non-readonly decision → not executable")
def test_plan_run_ollama_no_wrapper(minimal_workspace: Path) -> None:
    dec = _dec("ollama", command="./scripts/ollama/no-wrapper-xyz.sh", read_only=False)
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.executable is False  # kills `wrapper.read_only if wrapper else True`


@allure.title("plan_run ollama tier: empty command → 'and' guard falls through to fallback")
def test_plan_run_ollama_guard_and(minimal_workspace: Path) -> None:
    dec = _dec("ollama", command=None, read_only=True)
    plan = plan_run(dec, "task", minimal_workspace)
    # `target == "ollama" and command` is False (no command) → fallback branch.
    assert plan.dry_run_output == "No executor."  # kills `and` → `or`


@allure.title("plan_run rag tier: threads task/root/domains into search_rag, decision preserved")
def test_plan_run_rag_search_args(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_search(task, root, *, domains=None, limit=None):
        seen.update(task=task, root=root, domains=domains, limit=limit)
        return ["h"]

    monkeypatch.setattr(ex, "search_rag", fake_search)
    monkeypatch.setattr(ex, "format_hits", lambda task, hits: "FMT")
    dec = _dec("rag", domains=["config"])
    plan = plan_run(dec, "the task", minimal_workspace)
    assert seen["task"] == "the task"  # kills task=None
    assert seen["root"] == minimal_workspace  # kills root=None / root and ..
    assert seen["domains"] == ["config"]  # kills domains=None / dropped / `and None`
    assert plan.decision is dec  # kills decision=None
    assert plan.dry_run_output == "FMT"


@allure.title("plan_run cursor tier: exact guidance text, not executable, decision preserved")
def test_plan_run_cursor_exact(minimal_workspace: Path) -> None:
    dec = _dec("cursor")
    plan = plan_run(dec, "do a thing", minimal_workspace)
    expected = (
        "Open new Cursor chat.\n"
        "Task: do a thing\n"
        "Before paste: greedy-token audit-context && greedy-token rag \"<topic>\""
    )
    assert plan.dry_run_output == expected  # kills 'XX'/case string mutants
    assert plan.executable is False  # kills executable=None / executable=True
    assert plan.decision is dec  # kills decision=None


@allure.title("plan_run unknown tier: fallback text, not executable, decision preserved")
def test_plan_run_unknown_exact(minimal_workspace: Path) -> None:
    dec = _dec("weird-target")
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.dry_run_output == "No executor."
    assert plan.executable is False  # kills executable=None / executable=True
    assert plan.decision is dec  # kills decision=None


# --- execute_plan: command-less dry-run return ---


@allure.title("execute_plan with no command returns exit 0 and the dry-run output")
def test_execute_plan_no_command_exit_zero() -> None:
    plan = RunPlan(
        decision=_dec("rag"), command=None, dry_run_output="DRY", executable=True
    )
    code, out = execute_plan(plan)
    assert code == 0  # kills `return 1, ..`
    assert out == "DRY"


# --- _tool_output_weak: exact truth table ---


@allure.title("_tool_output_weak: exact truth table across filtered/exit-code branches")
def test_tool_output_weak_truth_table() -> None:
    with allure.step("empty filtered output → weak (kills first return True → False)"):
        assert ex._tool_output_weak("", 0) is True
        assert ex._tool_output_weak(".cursor/hooks/noise", 0) is True
    with allure.step("exit code outside (0,1) → weak (kills second return True → False)"):
        assert ex._tool_output_weak("data", 5) is True
    with allure.step("exit code 1 with content → NOT weak (kills (0,1) → (0,2))"):
        assert ex._tool_output_weak("data", 1) is False
        assert ex._tool_output_weak("data", 0) is False


# --- execute_task: root threading + guard + every tool/non-tool return ---


def _wire(monkeypatch, *, decision, plan, exec_ret=None, rag_ret="__none__", cap=None):
    monkeypatch.setattr(ex, "route_task", lambda task, root: (cap.__setitem__("route_root", root) if cap is not None else None) or decision)
    monkeypatch.setattr(ex, "plan_run", lambda d, task, root: (cap.__setitem__("plan_root", root) if cap is not None else None) or plan)
    if exec_ret is not None:
        monkeypatch.setattr(ex, "execute_plan", lambda p: exec_ret)
    if rag_ret != "__none__":
        def frag(task, root):
            if cap is not None:
                cap["rag_root"] = root
                cap["rag_task"] = task
            return rag_ret
        monkeypatch.setattr(ex, "_rag_fallback_output", frag)


@allure.title("execute_task threads the resolved root into route_task and plan_run")
def test_execute_task_threads_root(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cap: dict = {}
    dec = _dec("rag")
    plan = RunPlan(decision=dec, command=None, dry_run_output="D", executable=False)
    # Sentinel root makes `root or find_workspace_root()` vs `root and ..` observable.
    monkeypatch.setattr(ex, "find_workspace_root", lambda: minimal_workspace / "SENTINEL")
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(0, "D"), cap=cap)
    ex.execute_task("some task", minimal_workspace)
    assert cap["route_root"] == minimal_workspace  # kills root=None / root and .. / route_task(task,None)
    assert cap["plan_root"] == minimal_workspace  # kills plan_run(..,None)


@allure.title("execute_task cursor tier: exact refuse text + exit 1 + decision preserved")
def test_execute_task_cursor_exact(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dec = _dec("cursor")
    plan = RunPlan(decision=dec, command=None, dry_run_output="DRY", executable=False)
    _wire(monkeypatch, decision=dec, plan=plan)
    res = ex.execute_task("t", minimal_workspace)
    assert res.output == (
        "Refusing --execute: cursor tier requires expensive LLM (Agent chat).\n"
        "DRY"
    )
    assert res.exit_code == 1
    assert res.decision is dec


@allure.title("execute_task guard is 'executable AND command' (kills 'or')")
def test_execute_task_guard_and(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    # executable True but command None → 'and' skips the tool block; 'or' would enter it.
    plan = RunPlan(decision=dec, command=None, dry_run_output="", executable=True)
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(0, ""), rag_ret="RAGX")
    res = ex.execute_task("t", minimal_workspace)
    assert res.used_rag_fallback is False  # 'or' mutant would run the RAG fallback
    assert res.output == ""
    assert res.exit_code == 0


@allure.title("execute_task tool tier: weak rg + RAG fallback → exact output, exit 0, decision")
def test_execute_task_weak_with_rag(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cap: dict = {}
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(0, ""), rag_ret="RAGDATA", cap=cap)
    res = ex.execute_task("find baseUrl", minimal_workspace)
    note = f"rg: no useful matches for «{ex._extract_query_note('find baseUrl')}» → fallback RAG\n\n"
    assert res.output == note + "RAGDATA"
    assert res.used_rag_fallback is True
    assert res.exit_code == 0  # kills exit_code=None / exit_code=1
    assert res.decision is dec  # kills decision=None
    assert cap["rag_root"] == minimal_workspace  # kills _rag_fallback_output(task, None)


@allure.title("execute_task tool tier: weak rg + no RAG → raw output, exit=code, decision")
def test_execute_task_weak_no_rag(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(2, ""), rag_ret=None)
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.used_rag_fallback is False
    assert res.output == "rg x"  # out.strip() empty → dry_run_output
    assert res.exit_code == 2
    assert res.decision is dec  # kills decision=None


@allure.title("execute_task tool tier: filtered≠raw, short → appends RAG (exact note, exit 0)")
def test_execute_task_filtered_short_with_rag(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap: dict = {}
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=(0, ".cursor/hooks/noise\nbaseUrl"), rag_ret="RAGX", cap=cap,
    )
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.output == (
        "rg (without .cursor/hooks):\nbaseUrl\n"
        "\n---\nAdditional RAG:\n\nRAGX"
    )  # kills note= (drops rg header) and 'XX' string mutants
    assert res.used_rag_fallback is True
    assert res.exit_code == 0  # kills exit_code=None / exit_code=1
    assert res.decision is dec  # kills decision=None
    assert cap["rag_root"] == minimal_workspace  # kills _rag_fallback_output(task, None)


@allure.title("execute_task tool tier: filtered≠raw with exactly 3 lines → no RAG append (kills <3 boundary)")
def test_execute_task_filtered_three_lines_no_append(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    body = ".cursor/hooks/noise\nl1: baseUrl\nl2: baseUrl\nl3: baseUrl"  # filtered = 3 lines
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(0, body), rag_ret="RAGX")
    res = ex.execute_task("find baseUrl", minimal_workspace)
    # len(filtered.splitlines()) == 3 → `< 3` is False → no append (kills <=3 and <4)
    assert res.used_rag_fallback is False
    assert "Additional RAG" not in res.output
    assert res.output == "rg (without .cursor/hooks):\nl1: baseUrl\nl2: baseUrl\nl3: baseUrl\n"


@allure.title("execute_task tool tier: filtered≠raw, no RAG → note only, exit=code, decision")
def test_execute_task_filtered_no_rag(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=(1, ".cursor/hooks/noise\nbaseUrl"), rag_ret=None,
    )
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.output == "rg (without .cursor/hooks):\nbaseUrl\n"
    assert res.exit_code == 1  # kills exit_code=None / dropped (default 0)
    assert res.decision is dec  # kills decision=None
    assert res.used_rag_fallback is False


@allure.title("execute_task tool tier: filtered==raw (no noise) → filtered output, exit=code")
def test_execute_task_filtered_equals_raw(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(1, "baseUrl\nmore"))
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.output == "baseUrl\nmore"
    assert res.exit_code == 1  # kills dropped exit_code (default 0)
    assert res.decision is dec


@allure.title("execute_task non-tool executable tier: raw output, exit=code, decision")
def test_execute_task_nontool_executable(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("python", command="c", read_only=True)
    plan = RunPlan(decision=dec, command="c", dry_run_output="DRY", executable=True)
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(5, "OUT"))
    res = ex.execute_task("run", minimal_workspace)
    assert res.output == "OUT"
    assert res.exit_code == 5  # kills dropped exit_code (default 0)
    assert res.decision is dec  # kills decision=None


@allure.title("execute_task non-executable tier: final execute_plan output, exit=code, decision")
def test_execute_task_not_executable_final(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("ollama", command="c", read_only=False)
    plan = RunPlan(decision=dec, command="c", dry_run_output="DRY", executable=False)
    _wire(monkeypatch, decision=dec, plan=plan, exec_ret=(3, "OUT"))
    res = ex.execute_task("run", minimal_workspace)
    assert res.output == "OUT"
    assert res.exit_code == 3  # kills exit_code=None / dropped (default 0)
    assert res.decision is dec


# --- _rag_fallback_output: exact search_rag args on both calls ---


@allure.title("_rag_fallback_output: first call threads root; second call threads task/root")
def test_rag_fallback_output_thread_args(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_search(task, root, *, domains=None, limit=None):
        calls.append({"task": task, "root": root, "domains": domains, "limit": limit})
        return []  # force the second (domain-less) call

    monkeypatch.setattr(ex, "search_rag", fake_search)
    out = ex._rag_fallback_output("allure dashboard", minimal_workspace)
    assert out is None
    with allure.step("first call threads the real root (kills root=None)"):
        assert calls[0]["root"] == minimal_workspace
        assert calls[0]["domains"] == ["analytics"]
    with allure.step("second call threads task + root (kills task=None / root=None)"):
        assert calls[1]["task"] == "allure dashboard"
        assert calls[1]["root"] == minimal_workspace
        assert calls[1]["domains"] is None
