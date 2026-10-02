"""Mutation kill-tests for executors: exact fields on every return path.

Pins decision identity, exit codes, executable flags, root threading and the
dry-run / cursor / RAG strings so single-token mutants are caught with ``==``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import allure
from greedy_token import executors as ex
from greedy_token.executors import (
    PlanRunResult,
    RunPlan,
    TaskRunResult,
    execute_plan,
    plan_run,
    task_result_gate,
)
from greedy_token.result_contract import RESULT_NOT_EVALUATED, RESULT_PRODUCED
from greedy_token.result_gate import (
    GATE_ACCEPTED,
    GATE_BYPASSED,
    REASON_OUTPUT_EMPTY,
    REASON_UNVERIFIED_RESULT,
)
from greedy_token.router import RouteDecision
from greedy_token.subprocess_safe import UnsafeCommandError, format_invocation
from greedy_token.trust import approve_script

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
    dec = _dec(
        "tool",
        command="rg -n foo --max-count 50 .",
        read_only=True,
        tool="rg",
        command_argv=("rg", "-n", "foo", "--max-count", "50", "."),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.decision is dec
    assert plan.command == "rg -n foo --max-count 50 ."
    assert plan.dry_run_output == "rg -n foo --max-count 50 ."  # kills dry_run_output=None
    assert plan.executable is True
    assert plan.argv == dec.command_argv
    assert plan.cwd == minimal_workspace
    assert plan.authorization == "internal-tool:rg"


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
    assert plan.command == "python scripts/meta-sync-check.py"
    assert f"cwd={json.dumps(str(minimal_workspace))}" in plan.dry_run_output
    assert '"scripts/meta-sync-check.py"' in plan.dry_run_output
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
    assert plan.command == "./scripts/ollama/audit-skill.sh"
    assert f"cwd={json.dumps(str(minimal_workspace))}" in plan.dry_run_output
    assert plan.dry_run_output.endswith("  # pass args as needed")
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
    res = ex.execute_task("some task", minimal_workspace)
    assert cap["route_root"] == minimal_workspace  # kills root=None / root and .. / route_task(task,None)
    assert cap["plan_root"] == minimal_workspace  # kills plan_run(..,None)
    # A plain tuple never observed the process — started stays False.
    assert res.started is False  # kills _plan_started defaults None/True
    assert res.result_status == RESULT_NOT_EVALUATED  # kills default None


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
    assert res.output == ""  # observed output only — never the invocation text
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
        "rg (without agent-internal dirs):\nbaseUrl\n"
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
    assert res.output == "rg (without agent-internal dirs):\nl1: baseUrl\nl2: baseUrl\nl3: baseUrl\n"


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
    assert res.output == "rg (without agent-internal dirs):\nbaseUrl\n"
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
    assert res.started is False  # plain tuple never observed a start
    assert res.result_status == RESULT_NOT_EVALUATED


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




def _untrusted_py(root: Path) -> None:
    script = root / "scripts" / "x.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env python\nprint('x')\n", encoding="utf-8")


# --- plan_run tool branch: refusal-plane exact fields ---


@allure.title("plan_run tool tier: missing argv refuses with exact builder message")
def test_plan_run_tool_missing_argv_refusal(minimal_workspace: Path) -> None:
    dec = _dec("tool", command="rg x .", read_only=True, tool="rg")
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.decision is dec  # kills decision=None
    assert plan.command == "rg x ."  # kills command=None
    assert plan.dry_run_output == "rg x ."  # kills dry_run_output=None
    assert plan.executable is False
    assert plan.refusal_reason == (
        "tool command was not produced by the internal argv builder"
    )  # kills message None/"XX…XX"/UPPER and str(None) mutants
    assert plan.refusal_code == ""  # kills refusal_code=None


@allure.title("plan_run tool tier: decision.tool is validated — jq argv is executable")
def test_plan_run_tool_jq_argv(minimal_workspace: Path) -> None:
    dec = _dec(
        "tool",
        command="jq .key docs/x.json",
        read_only=True,
        tool="jq",
        command_argv=("jq", ".key", "docs/x.json"),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    # tool=None would resolve expected='rg' and refuse the jq argv.
    assert plan.executable is True
    assert plan.authorization == "internal-tool:jq"
    assert plan.argv == ("jq", ".key", "docs/x.json")


@allure.title("plan_run tool tier: a coded UnsafeCommandError propagates its refusal code")
def test_plan_run_tool_coded_refusal(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise UnsafeCommandError("custom refusal", code="symlink")

    monkeypatch.setattr(ex, "trusted_tool_invocation", boom)
    dec = _dec(
        "tool",
        command="rg x .",
        read_only=True,
        tool="rg",
        command_argv=("rg", "x", "."),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.executable is False
    assert plan.refusal_reason == "custom refusal"
    # kills dropped kwarg, getattr(None,…), and wrong-attribute-name mutants.
    assert plan.refusal_code == "symlink"


@allure.title("plan_run tool tier: an uncoded OSError leaves refusal_code empty")
def test_plan_run_tool_uncoded_oserror(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise OSError("io gone")

    monkeypatch.setattr(ex, "trusted_tool_invocation", boom)
    dec = _dec(
        "tool",
        command="rg x .",
        read_only=True,
        tool="rg",
        command_argv=("rg", "x", "."),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.executable is False
    assert plan.refusal_reason == "io gone"
    # kills getattr defaults None / dropped / "XXXX".
    assert plan.refusal_code == ""


# --- plan_run script branch: trust lists, legacy parse and refusal fields ---


@allure.title("plan_run script tier: refusal surfaces the trusted argv in dry-run")
def test_plan_run_script_refusal_dry_run_fields(minimal_workspace: Path) -> None:
    _untrusted_py(minimal_workspace)
    dec = _dec(
        "python",
        command="python scripts/x.py",
        read_only=True,
        command_argv=("python", "scripts/x.py"),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.decision is dec  # kills decision=None
    assert plan.executable is False
    # kills `and False`, format_invocation(.., None), and dry_run=None mutants.
    assert plan.dry_run_output == format_invocation(
        ("python", "scripts/x.py"), minimal_workspace
    )
    assert plan.refusal_reason == (
        "script is not registered or approved in the local trust manifest: "
        "'scripts/x.py'"
    )
    assert plan.refusal_code == "not_approved"


@allure.title("plan_run script tier: argv set but cwd missing still parses the command")
def test_plan_run_script_argv_without_cwd(minimal_workspace: Path) -> None:
    _untrusted_py(minimal_workspace)
    dec = _dec(
        "python",
        command="python scripts/x.py",
        read_only=True,
        command_argv=("python", "scripts/x.py"),
        command_cwd=None,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    # `argv is None or cwd is None` → legacy parse runs; `and` would pass a
    # None cwd into trusted_script_argv and crash instead of refusing.
    assert plan.executable is False
    assert plan.refusal_code == "not_approved"
    # argv present but command_cwd unset → dry-run falls back to the raw
    # command string (kills `or` and `cwd is None` condition mutants).
    assert plan.dry_run_output == "python scripts/x.py"


@allure.title("plan_run script tier: cwd set but argv missing falls back to command")
def test_plan_run_script_cwd_without_argv(minimal_workspace: Path) -> None:
    _untrusted_py(minimal_workspace)
    dec = _dec(
        "python",
        command="python scripts/x.py",
        read_only=True,
        command_argv=None,
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.executable is False
    assert plan.refusal_code == "not_approved"
    # `argv is None and cwd is not None` mutant would call
    # format_invocation(None, …) and crash on the None iteration.
    assert plan.dry_run_output == "python scripts/x.py"


@allure.title("plan_run script tier: deprecated list is ignored for write-tier decisions")
def test_plan_run_script_deprecated_only_when_readonly(minimal_workspace: Path) -> None:
    _untrusted_py(minimal_workspace)
    (minimal_workspace / ".greedy-token.yaml").write_text(
        "routes_file: workspace-routes.yaml\n"
        "trusted_script_paths:\n  - scripts/x.py\n",
        encoding="utf-8",
    )
    dec = _dec("python", command="python scripts/x.py", read_only=False)
    plan = plan_run(dec, "task", minimal_workspace)
    # The `or True` mutant would load the deprecated list and switch the
    # refusal to the migration message.
    assert plan.executable is False
    assert plan.refusal_code == "not_approved"
    assert plan.refusal_reason.startswith("script is not registered or approved")


@allure.title("plan_run script tier: manifest approval is ignored for write-tier decisions")
def test_plan_run_script_manifest_only_when_readonly(minimal_workspace: Path) -> None:
    _untrusted_py(minimal_workspace)
    approve_script(minimal_workspace, "scripts/x.py")
    dec = _dec("python", command="python scripts/x.py", read_only=False)
    plan = plan_run(dec, "task", minimal_workspace)
    # The `or True` mutant would load manifest approvals and mark it executable.
    assert plan.executable is False
    assert plan.refusal_code == "not_approved"


@allure.title("plan_run script tier: cd outside the workspace root is refused at parse")
def test_plan_run_script_cd_outside_root(minimal_workspace: Path) -> None:
    outside = minimal_workspace.parent
    dec = _dec(
        "python", command=f"cd {outside} && ./x.sh", read_only=True
    )
    plan = plan_run(dec, "task", minimal_workspace)
    # workspace_root=None would skip confinement; trusted_script_argv then
    # refuses with a different message — the parse-time refusal is pinned.
    assert plan.executable is False
    assert plan.refusal_reason.startswith("cwd is outside workspace root")


@allure.title("plan_run script tier: cd inside the workspace is refused by cwd equality")
def test_plan_run_script_cd_inside_subdir(minimal_workspace: Path) -> None:
    _untrusted_py(minimal_workspace)
    dec = _dec(
        "python", command=f"cd {minimal_workspace}/docs && ./x.sh", read_only=True
    )
    plan = plan_run(dec, "task", minimal_workspace)
    # `cwd = parsed_cwd and root` would silently re-root the cwd and continue
    # into the trust checks instead of refusing here.
    assert plan.executable is False
    assert plan.refusal_reason == "script cwd must equal the workspace root"


@allure.title("plan_run script tier: an uncoded OSError leaves refusal_code empty")
def test_plan_run_script_uncoded_oserror(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _untrusted_py(minimal_workspace)
    def boom(*args, **kwargs):
        raise OSError("io gone")

    monkeypatch.setattr(ex, "trusted_script_argv", boom)
    dec = _dec(
        "python",
        command="python scripts/x.py",
        read_only=True,
        command_argv=("python", "scripts/x.py"),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "task", minimal_workspace)
    assert plan.executable is False
    assert plan.refusal_reason == "io gone"
    # kills getattr defaults None / "XXXX" in the script refusal branch.
    assert plan.refusal_code == ""


# --- execute_plan: timeout and contract-verdict branches ---


def _exec_plan(root: Path, **kw) -> RunPlan:
    base = dict(
        decision=_dec("python", command="python scripts/x.py", read_only=True),
        command="python scripts/x.py",
        dry_run_output="D",
        executable=True,
        argv=("python", "scripts/x.py"),
        cwd=root,
        authorization="wrapper:scripts/x.py",
        script_type="python",
    )
    base.update(kw)
    return RunPlan(**base)


@allure.title("execute_plan timeout: the process started and was killed (exit 124)")
def test_execute_plan_timeout_started(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    res = execute_plan(_exec_plan(minimal_workspace))
    assert res.exit_code == 124
    assert "timed out after" in res.output
    # It did start — kills started=None / dropped / False mutants.
    assert res.started is True


@allure.title("execute_plan: shell scripts are never contract-evaluated")
def test_execute_plan_shell_not_evaluated(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout='{"ok": true}', stderr="", returncode=0
        ),
    )
    plan = _exec_plan(minimal_workspace, script_type="shell")
    res = execute_plan(plan)
    assert res.exit_code == 0
    assert res.started is True
    # `or True` would evaluate the claim and report produced for a shell run.
    assert res.result_status == RESULT_NOT_EVALUATED


# --- execute_task: PlanRunResult fields propagate through every branch ---


@allure.title("execute_task tool tier: observed started=True survives the RAG fallback")
def test_execute_task_weak_rag_started_true(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=PlanRunResult(0, "", started=True), rag_ret="RAGDATA",
    )
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.used_rag_fallback is True
    # kills started=None / dropped (default False) on the weak+RAG return.
    assert res.started is True


@allure.title("execute_task tool tier: weak output without RAG keeps started=True")
def test_execute_task_weak_no_rag_started_true(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=PlanRunResult(2, "", started=True), rag_ret=None,
    )
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.used_rag_fallback is False
    assert res.exit_code == 2
    # kills started=None / dropped on the weak-without-RAG return.
    assert res.started is True


@allure.title("execute_task tool tier: filtered+RAG append keeps started=True")
def test_execute_task_filtered_rag_started_true(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=PlanRunResult(0, ".cursor/hooks/noise\nbaseUrl", started=True),
        rag_ret="RAGX",
    )
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.used_rag_fallback is True
    # kills started=None / dropped on the filtered+RAG return.
    assert res.started is True


@allure.title("execute_task tool tier: filtered without RAG keeps started=True")
def test_execute_task_filtered_no_rag_started_true(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=PlanRunResult(1, ".cursor/hooks/noise\nbaseUrl", started=True),
        rag_ret=None,
    )
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.used_rag_fallback is False
    assert res.exit_code == 1
    # kills started=None / dropped on the filtered-no-RAG return.
    assert res.started is True


@allure.title("execute_task tool tier: unfiltered output keeps started=True")
def test_execute_task_unfiltered_started_true(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("tool", command="rg x", read_only=True)
    plan = RunPlan(decision=dec, command="rg x", dry_run_output="rg x", executable=True)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=PlanRunResult(1, "baseUrl\nmore", started=True),
    )
    res = ex.execute_task("find baseUrl", minimal_workspace)
    assert res.exit_code == 1
    # kills started=None / dropped on the filtered==raw return.
    assert res.started is True


@allure.title("execute_task final return: started and result_status propagate")
def test_execute_task_final_plan_result_fields(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _dec("ollama", command="c", read_only=False)
    plan = RunPlan(decision=dec, command="c", dry_run_output="DRY", executable=False)
    _wire(
        monkeypatch, decision=dec, plan=plan,
        exec_ret=PlanRunResult(3, "OUT", started=True, result_status=RESULT_PRODUCED),
    )
    res = ex.execute_task("run", minimal_workspace)
    assert res.output == "OUT"
    assert res.exit_code == 3
    # kills _plan_started(None) / dropped started / result_status=None and
    # _plan_result_status(None) / dropped result_status on the final return.
    assert res.started is True
    assert res.result_status == RESULT_PRODUCED


# --- task_result_gate: tier-native usefulness feeds the gate ---


@allure.title("task_result_gate tool tier: weak filtered output is never useful")
def test_task_result_gate_tool_weak(minimal_workspace: Path) -> None:
    dec = _dec("tool", command="rg x", read_only=True, tool="rg")
    res = TaskRunResult(
        decision=dec, output=".cursor/hooks/noise", exit_code=0, started=True
    )
    gate = task_result_gate(res, dec)
    # `!=`/`"XXtoolXX"`/`"TOOL"` mutants route to the raw-output check and
    # would ACCEPT the noise; dropping `not` or `useful=None` likewise.
    assert gate.action == GATE_BYPASSED
    assert gate.reason == REASON_OUTPUT_EMPTY
    assert gate.may_answer is False
    assert gate.savings_eligible is False


@allure.title("task_result_gate tool tier: real matches are a useful answer")
def test_task_result_gate_tool_useful(minimal_workspace: Path) -> None:
    dec = _dec("tool", command="rg x", read_only=True, tool="rg")
    res = TaskRunResult(
        decision=dec, output="real match data\n", exit_code=0, started=True
    )
    gate = task_result_gate(res, dec)
    # `_tool_output_weak(out, None)` sees exit None ∉ (0,1) → weak → bypass.
    assert gate.action == GATE_ACCEPTED
    assert gate.may_answer is True
    assert gate.savings_eligible is True


@allure.title("task_result_gate contract tier: empty output is unverified, not an answer")
def test_task_result_gate_python_silent(minimal_workspace: Path) -> None:
    dec = _dec("python", command="python scripts/x.py", read_only=True)
    res = TaskRunResult(
        decision=dec, output="", exit_code=0, started=True
    )
    gate = task_result_gate(res, dec)
    # useful=None / dropped output_useful would normalise to True and ACCEPT;
    # tier=None falls out of CONTRACT_TIERS and reports output_empty instead.
    assert gate.action == GATE_BYPASSED
    assert gate.reason == REASON_UNVERIFIED_RESULT
    assert gate.tier == "python"
    assert gate.may_answer is False
    assert gate.savings_eligible is False


# --- plan_run: prompt-derived args (args_from_prompt) — fail-open taxonomy ---

_ARGS_SPEC = [{"regex": r"([0-9]+) коммит", "args": "--count {0}"}]


def _args_route(spec=_ARGS_SPEC, **kw) -> dict:
    route = {
        "id": "python-git-recent",
        "target": "python",
        "read_only": True,
        "command": "python scripts/git-recent.py",
        "params": ["args"],
        "args_from_prompt": spec,
    }
    route.update(kw)
    return route


def _routes_cfg(route: dict) -> dict:
    return {"routes": [route]}


def _args_dec() -> RouteDecision:
    return _dec(
        "python",
        route_id="python-git-recent",
        command="python scripts/git-recent.py",
        read_only=True,
    )


@allure.title("_prompt_derived_args: blank task and loader failure are fail-open")
def test_prompt_derived_args_task_and_loader_edges(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _args_dec()
    assert ex._prompt_derived_args("", dec, minimal_workspace) == ()
    assert ex._prompt_derived_args("   ", dec, minimal_workspace) == ()

    def boom(*args, **kwargs):
        raise RuntimeError("routes gone")

    monkeypatch.setattr("greedy_token.paths.load_routes_config", boom)
    assert ex._prompt_derived_args("5 коммитов", dec, minimal_workspace) == ()


@allure.title("_prompt_derived_args: unknown route, missing args contract, bad spec → ()")
def test_prompt_derived_args_contract_gates(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _args_dec()
    cfgs = [
        {"routes": ["junk", {"id": "python-other"}]},  # route id not found
        _routes_cfg(_args_route(params=[])),  # no params: [args] contract
        _routes_cfg(_args_route("nope")),  # args_from_prompt is not a list
    ]
    for cfg in cfgs:
        monkeypatch.setattr(
            "greedy_token.paths.load_routes_config",
            lambda *a, _cfg=cfg, **k: _cfg,
        )
        assert ex._prompt_derived_args("5 коммитов", dec, minimal_workspace) == ()


@allure.title("_prompt_derived_args: scalar params normalise to the args contract")
def test_prompt_derived_args_scalar_params(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "greedy_token.paths.load_routes_config",
        lambda *a, **k: _routes_cfg(_args_route(params="args")),
    )
    assert ex._prompt_derived_args(
        "покажи 5 коммитов", _args_dec(), minimal_workspace
    ) == ("--count", "5")


@allure.title("_prompt_derived_args: regex miss, whitespace render, unparseable render → ()")
def test_prompt_derived_args_render_edges(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dec = _args_dec()
    cfg = _routes_cfg(_args_route())
    monkeypatch.setattr(
        "greedy_token.paths.load_routes_config", lambda *a, **k: cfg
    )
    # The spec regex never hits a prompt without digits.
    assert ex._prompt_derived_args("покажи коммиты", dec, minimal_workspace) == ()

    cfg["routes"][0]["args_from_prompt"] = [
        {"regex": "([0-9]+)", "args": "   "}
    ]
    assert ex._prompt_derived_args("5", dec, minimal_workspace) == ()

    cfg["routes"][0]["args_from_prompt"] = [
        {"regex": "([0-9]+)", "args": "--count '{0}"}
    ]
    assert ex._prompt_derived_args("5", dec, minimal_workspace) == ()


@allure.title("plan_run: derived args land after the fixed command argv")
def test_plan_run_prompt_derived_args_order(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _untrusted_py(minimal_workspace)
    approve_script(minimal_workspace, "scripts/x.py")
    monkeypatch.setattr(
        "greedy_token.paths.load_routes_config",
        lambda *a, **k: _routes_cfg(_args_route()),
    )
    dec = _dec(
        "python",
        route_id="python-git-recent",
        command="python scripts/x.py --fixed",
        read_only=True,
        command_argv=("python", "scripts/x.py", "--fixed"),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "покажи 5 коммитов", minimal_workspace)
    assert plan.executable is True
    # The fixed command args stay ahead — same contract as invoke --args.
    assert plan.argv is not None
    assert plan.argv[-3:] == ("--fixed", "--count", "5")


@allure.title("plan_run: a prompt-derived arg escaping the workspace is refused")
def test_plan_run_prompt_derived_args_confined(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _untrusted_py(minimal_workspace)
    approve_script(minimal_workspace, "scripts/x.py")
    spec = [{"regex": r"show (.*)", "args": "{0}"}]
    monkeypatch.setattr(
        "greedy_token.paths.load_routes_config",
        lambda *a, **k: _routes_cfg(_args_route(spec)),
    )
    dec = _dec(
        "python",
        route_id="python-git-recent",
        command="python scripts/x.py",
        read_only=True,
        command_argv=("python", "scripts/x.py"),
        command_cwd=minimal_workspace,
    )
    plan = plan_run(dec, "show ../secrets", minimal_workspace)
    assert plan.executable is False
    assert "escapes workspace" in plan.refusal_reason
