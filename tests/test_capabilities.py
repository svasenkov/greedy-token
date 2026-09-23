"""Derived capability view + invoke-by-id — Step 3 surface.

The view is computed at call time from merged routes (bundled + workspace
overlay), the wrapper registry, the local trust manifest, the filesystem, and
plan_run's refusal classification — no second registry.  Invoke goes through
the exact ``run --execute`` guarded path; refused invocations carry the
readiness class, exit non-zero, start nothing, and emit Step-1 telemetry
(planned, authorized=false, saved=0).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import allure
from greedy_token.capabilities import (
    ADVISORY_ONLY,
    CONSUMER_ONLY,
    DISABLED_OR_SHADOW,
    MISSING_FILE,
    NOT_APPROVED,
    READY,
    STALE_BYTES,
    TOOL_UNAVAILABLE,
    TRUST_WRAPPER_COVERED_INERT,
    WRITE_NOT_INVOCABLE,
    collect_capabilities,
)
from greedy_token.capabilities_invoke import (
    REFUSAL_INVALID_PARAMS,
    REFUSAL_UNKNOWN_OPERATION,
    invoke_capability,
)
from greedy_token.pipeline import parse_pipeline, run_pipeline
from greedy_token.trust import approve_script
from greedy_token.usage import load_events, log_path

pytestmark = [
    allure.epic("Capabilities"),
    allure.parent_suite("Capabilities"),
    allure.feature("Derived capability view"),
    allure.suite("Derived capability view"),
]


@pytest.fixture(autouse=True)
def _isolated_trust_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test trust home — never read the developer's real manifest."""
    home = tmp_path / "gt-home"
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    return home


def _ops(view) -> dict:
    return {op.id: op for op in view.ops}


def _events() -> list[dict]:
    events, _skipped = load_events(log_path())
    return events


def _script(root: Path, relative: str, content: str) -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _fake_rg(root: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """An executable literally named ``rg`` — trusted_tool_invocation checks
    executable_name(argv[0]), so a fake must carry the real name."""
    fake = _script(root, "scripts/fake-bin/rg", body)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("GREEDY_TOKEN_RG", str(fake))
    return fake


@allure.story("Readiness taxonomy")
@allure.title("View derives every readiness class from route+fs+trust facts")
def test_view_readiness_classes(minimal_workspace: Path) -> None:
    view = collect_capabilities(minimal_workspace)
    ops = _ops(view)

    with allure.step("wrapper-authorized read-only route is ready+invocable"):
        cap = ops["python-meta-sync-check"]
        assert cap.readiness == READY
        assert cap.invocable
        assert cap.authorization == "wrapper:scripts/meta-sync-check.py"
        assert cap.source == "route"
        assert cap.origin == "workspace"
        assert cap.contract == "script-canon"

    with allure.step("route pointing at a missing script is missing_file"):
        cap = ops["python-openapi-diff"]
        assert cap.readiness == MISSING_FILE
        assert not cap.invocable
        assert cap.script_path == "scripts/openapi-diff.py"

    with allure.step("missing script marked consumer repo is consumer_only"):
        cap = ops["python-resolve-testops-project"]
        assert cap.readiness == CONSUMER_ONLY
        assert "consumer repo" in cap.reason

    with allure.step("enabled: false route is disabled_or_shadow"):
        cap = ops["python-auth-storage-probe"]
        assert cap.readiness == DISABLED_OR_SHADOW
        assert cap.status == "inactive"

    with allure.step("expired shadow_until route is inactive, not shadow"):
        cap = ops["python-provider-balance"]
        assert cap.readiness == DISABLED_OR_SHADOW
        assert cap.status == "inactive"

    with allure.step("read_only: false routes are visible but never invocable"):
        for rid in ("python-phase1-rsync", "python-google-sheets", "ollama-inventory"):
            assert ops[rid].readiness == WRITE_NOT_INVOCABLE, rid
            assert not ops[rid].invocable

    with allure.step("existing unapproved script is not_approved"):
        _script(minimal_workspace, "scripts/git-recent.py", "print('x')\n")
        view = collect_capabilities(minimal_workspace)
        cap = _ops(view)["python-git-recent"]
        assert cap.readiness == NOT_APPROVED
        assert not cap.invocable

    with allure.step("rag routes are ready but never invocable"):
        cap = ops["rag-config-keys"]
        assert cap.readiness == READY
        assert not cap.invocable
        assert cap.tier == "rag"

    with allure.step("wrapper-only ops appear under their wrapper id"):
        cap = ops["classify-file"]
        assert cap.source == "wrapper"
        assert cap.origin == "registry"
        assert cap.readiness == READY
        assert cap.invocable
        assert cap.params == ("args",)
        assert ops["apply-inventory"].readiness == WRITE_NOT_INVOCABLE

    with allure.step("bundled cursor fallback is advisory_only"):
        cursor_ops = [op for op in view.ops if op.tier == "cursor"]
        assert cursor_ops
        assert all(op.readiness == ADVISORY_ONLY for op in cursor_ops)
        assert not any(op.invocable for op in cursor_ops)


@allure.story("Readiness taxonomy")
@allure.title("Tool ops follow binary resolvability (rg override / disabled)")
def test_tool_readiness(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_rg(minimal_workspace, monkeypatch, "#!/bin/sh\nexit 0\n")

    cap = _ops(collect_capabilities(minimal_workspace))["tool-rg-search"]
    assert cap.readiness == READY
    assert cap.invocable
    assert cap.params == ("query",)
    assert cap.authorization == "internal-tool:rg"

    monkeypatch.setenv("GREEDY_TOKEN_DISABLE_EXTERNAL_TOOLS", "1")
    cap = _ops(collect_capabilities(minimal_workspace))["tool-rg-search"]
    assert cap.readiness == TOOL_UNAVAILABLE
    assert not cap.invocable


def _set_rg_search_paths(workspace: Path, paths: list[str]) -> None:
    overlay = workspace / "workspace-routes.yaml"
    data = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    route = next(r for r in data["routes"] if r["id"] == "tool-rg-search")
    route["search_paths"] = paths
    overlay.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@allure.story("Readiness taxonomy")
@allure.title("Stale rg search_paths stay visible: ready + missing_paths detail")
def test_rg_missing_search_paths_visible(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_rg(minimal_workspace, monkeypatch, "#!/bin/sh\nexit 0\n")
    _set_rg_search_paths(minimal_workspace, ["docs", "gone-dir"])

    cap = _ops(collect_capabilities(minimal_workspace))["tool-rg-search"]
    with allure.step("ready+invocable, but the stale dir is reported"):
        assert cap.readiness == READY
        assert cap.invocable
        assert cap.missing_paths == ("gone-dir",)
        assert "gone-dir" in cap.reason
        assert cap.to_dict()["missing_paths"] == ["gone-dir"]


@allure.story("Readiness taxonomy")
@allure.title("Every search_path missing — '.' fallback is stated in reason")
def test_rg_all_search_paths_missing(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_rg(minimal_workspace, monkeypatch, "#!/bin/sh\nexit 0\n")
    _set_rg_search_paths(minimal_workspace, ["gone-a", "gone-b"])

    cap = _ops(collect_capabilities(minimal_workspace))["tool-rg-search"]
    assert cap.readiness == READY
    assert cap.invocable
    assert cap.missing_paths == ("gone-a", "gone-b")
    assert "falls back to '.'" in cap.reason


@allure.story("Readiness taxonomy")
@allure.title("jq route with a missing json_path is missing_file, not invocable")
def test_jq_missing_json_path(minimal_workspace: Path) -> None:
    overlay = minimal_workspace / "workspace-routes.yaml"
    data = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    route = next(r for r in data["routes"] if r["id"] == "tool-jq-manifest")
    route["json_path"] = "gone/x.json"
    overlay.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    cap = _ops(collect_capabilities(minimal_workspace))["tool-jq-manifest"]
    assert cap.readiness == MISSING_FILE
    assert not cap.invocable
    assert "gone/x.json" in cap.reason


@allure.story("Invoke")
@allure.title("Invoke rg with a stale search_path skips it instead of rg exit 2")
def test_invoke_tool_op_skips_missing_paths(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_rg(
        minimal_workspace,
        monkeypatch,
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    )
    _set_rg_search_paths(minimal_workspace, ["docs", "gone-dir"])

    result = invoke_capability(minimal_workspace, "tool-rg-search", query="x")
    assert result.executed is True
    assert result.exit_code == 0
    assert result.missing_paths == ("gone-dir",)
    with allure.step("fake rg echoes argv — the missing dir never reaches it"):
        assert "gone-dir" not in result.output
        assert "docs" in result.output


@allure.story("Readiness taxonomy")
@allure.title("Manifest states: approved→ready, modified→stale_bytes, wrapper→inert")
def test_view_manifest_states(minimal_workspace: Path) -> None:
    with allure.step("manifest-approved script route becomes ready"):
        _script(minimal_workspace, "scripts/git-recent.py", "print('v1')\n")
        approve_script(minimal_workspace, "scripts/git-recent.py")
        cap = _ops(collect_capabilities(minimal_workspace))["python-git-recent"]
        assert cap.readiness == READY
        assert cap.invocable
        assert cap.authorization == "manifest:scripts/git-recent.py"
        assert cap.trust_entry == "ok"

    with allure.step("bytes changed after approval → stale_bytes at listing time"):
        _script(minimal_workspace, "scripts/git-recent.py", "print('v2')\n")
        cap = _ops(collect_capabilities(minimal_workspace))["python-git-recent"]
        assert cap.readiness == STALE_BYTES
        assert not cap.invocable
        assert cap.trust_entry == STALE_BYTES

    with allure.step("wrapper-covered manifest entry is marked inert"):
        approve_script(minimal_workspace, "scripts/meta-sync-check.py")
        _script(minimal_workspace, "scripts/meta-sync-check.py", "print('tampered')\n")
        cap = _ops(collect_capabilities(minimal_workspace))["python-meta-sync-check"]
        # Wrapper authorization wins; the stale manifest row grants nothing.
        assert cap.readiness == READY
        assert cap.trust_entry == TRUST_WRAPPER_COVERED_INERT


@allure.story("Derivation")
@allure.title("A new route in the workspace overlay appears with no code change")
def test_new_overlay_route_appears(minimal_workspace: Path) -> None:
    overlay = minimal_workspace / "workspace-routes.yaml"
    data = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    data["routes"].append(
        {
            "id": "python-brand-new-op",
            "target": "python",
            "read_only": True,
            "patterns": ["brand new op"],
            "command": "python scripts/brand-new.py",
            "note": "fixture derivation proof",
        }
    )
    overlay.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    ops = _ops(collect_capabilities(minimal_workspace))
    assert "python-brand-new-op" in ops
    cap = ops["python-brand-new-op"]
    assert cap.origin == "workspace"
    assert cap.readiness == MISSING_FILE  # script does not exist yet
    assert cap.script_path == "scripts/brand-new.py"

    _script(minimal_workspace, "scripts/brand-new.py", "print('{\"ok\": true}')\n")
    cap = _ops(collect_capabilities(minimal_workspace))["python-brand-new-op"]
    assert cap.readiness == NOT_APPROVED  # exists, but never approved


@allure.story("Invoke")
@allure.title("Invoke of a ready op runs the guarded path and logs one operation")
def test_invoke_ready_op(minimal_workspace: Path) -> None:
    result = invoke_capability(minimal_workspace, "python-meta-sync-check")
    with allure.step("result fields"):
        assert result.executed is True
        assert result.exit_code == 0
        assert "meta-sync-check-ok" in result.output
        assert result.result_status == "produced"
        assert result.gate_action == "accepted"
        assert result.outcome == "success"
        assert result.operation_id

    with allure.step("Step-1 telemetry: executed request + correlated outcome"):
        events = _events()
        request = next(e for e in events if e.get("cmd") == "invoke")
        outcome = next(e for e in events if e.get("event") == "route_outcome")
        assert request["phase"] == "executed"
        assert request["authorized"] is True
        assert request["executor"]["executed"] is True
        assert request["route_id"] == "python-meta-sync-check"
        assert request["result_status"] == "produced"
        assert request["gate_action"] == "accepted"
        assert outcome["outcome"] == "success"
        assert outcome["operation_id"] == request["operation_id"]


@allure.story("Invoke")
@allure.title("Silent shell wrapper invoke: bypassed/output_empty — saved=0, honest output")
def test_invoke_empty_wrapper_output_claims_nothing(
    minimal_workspace: Path,
) -> None:
    """A shell wrapper exiting 0 with empty stdout used to report
    outcome=success and claim savings — usefulness was judged on the
    invocation description, not the observed output."""
    script = minimal_workspace / "scripts" / "ollama" / "classify-file.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    result = invoke_capability(
        minimal_workspace, "classify-file", args="input.txt"
    )
    with allure.step("gate ruling: empty observed output → no answer, no savings"):
        assert result.executed is True
        assert result.exit_code == 0
        assert result.output == ""  # the invocation text is not a result
        assert result.gate_action == "bypassed"
        assert result.gate_reason == "output_empty"
        assert result.result_status == "not_evaluated"
        assert result.outcome == "failure"
    with allure.step("telemetry: zero savings with the empty_result exclusion"):
        events = _events()
        request = next(e for e in events if e.get("cmd") == "invoke")
        assert request["phase"] == "executed"
        assert request["cursor_saved"] == 0
        assert request["savings_eligible"] is False
        assert request["savings_exclusion"] == "empty_result"
        outcome = next(e for e in events if e.get("event") == "route_outcome")
        assert outcome["outcome"] == "failure"
        assert outcome["cursor_saved"] == 0


@allure.story("Invoke")
@allure.title("Invoke of an unapproved op refuses: nothing starts, saved=0")
def test_invoke_not_approved_refuses(minimal_workspace: Path) -> None:
    marker = minimal_workspace / "marker-ran.txt"
    _script(
        minimal_workspace,
        "scripts/git-recent.py",
        f"open({str(marker)!r}, 'w').write('ran')\n",
    )
    result = invoke_capability(minimal_workspace, "python-git-recent")
    with allure.step("refusal fields"):
        assert result.invocable is False
        assert result.executed is False
        assert result.exit_code == 1
        assert result.refusal_code == NOT_APPROVED
        assert result.gate_action == "bypassed"
        assert result.gate_reason == "not_started"
        assert result.outcome == "failure"
    with allure.step("the script never started"):
        assert not marker.exists()
    with allure.step("telemetry: planned, authorized=false, no savings"):
        events = _events()
        request = next(e for e in events if e.get("cmd") == "invoke")
        assert request["phase"] == "planned"
        assert request["authorized"] is False
        assert request["executor"]["executed"] is False
        assert request["cursor_saved"] == 0
        assert request["savings_exclusion"] == "not_executed"
        outcome = next(e for e in events if e.get("event") == "route_outcome")
        assert outcome["outcome"] == "failure"
        assert outcome["cursor_saved"] == 0
        assert outcome["operation_id"] == request["operation_id"]


@allure.story("Invoke")
@allure.title("Write and advisory ops are refused by policy, never executed")
def test_invoke_write_and_advisory_refused(minimal_workspace: Path) -> None:
    result = invoke_capability(minimal_workspace, "apply-inventory")
    assert result.exit_code == 1
    assert result.refusal_code == WRITE_NOT_INVOCABLE
    assert result.executed is False

    cursor_op = next(
        op for op in collect_capabilities(minimal_workspace).ops if op.tier == "cursor"
    )
    result = invoke_capability(minimal_workspace, cursor_op.id)
    assert result.exit_code == 1
    assert result.refusal_code == ADVISORY_ONLY
    assert result.executed is False


@allure.story("Invoke")
@allure.title("Unknown op id and undeclared params are caller errors (exit 2)")
def test_invoke_unknown_and_bad_params(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = invoke_capability(minimal_workspace, "no-such-op")
    assert result.exit_code == 2
    assert result.refusal_code == REFUSAL_UNKNOWN_OPERATION

    _fake_rg(minimal_workspace, monkeypatch, "#!/bin/sh\nexit 0\n")

    result = invoke_capability(minimal_workspace, "tool-rg-search")
    assert result.exit_code == 2
    assert result.refusal_code == REFUSAL_INVALID_PARAMS

    result = invoke_capability(
        minimal_workspace, "python-meta-sync-check", args="--force"
    )
    assert result.exit_code == 2
    assert result.refusal_code == REFUSAL_INVALID_PARAMS
    assert "fixed argv" in result.refusal_reason


@allure.story("Invoke")
@allure.title("Tool op invoke builds argv from --query through the internal builder")
def test_invoke_tool_op(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_rg(
        minimal_workspace,
        monkeypatch,
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    )

    result = invoke_capability(
        minimal_workspace, "tool-rg-search", query="needle-term"
    )
    assert result.executed is True
    assert result.exit_code == 0
    assert "needle-term" in result.output
    assert result.tier == "tool"


@allure.story("Invoke")
@allure.title("Invoke rg --query stays a literal pattern — '--' ends option parsing")
def test_invoke_tool_op_query_is_not_an_option(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_rg(
        minimal_workspace,
        monkeypatch,
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    )

    result = invoke_capability(
        minimal_workspace, "tool-rg-search", query="--version"
    )
    assert result.executed is True
    echoed = result.output.splitlines()
    with allure.step("argv: options … --max-count N -- <pattern> <paths…>"):
        sep = echoed.index("--")
        assert echoed[sep + 1] == "--version"  # the pattern, not rg --version
        assert echoed[sep + 2 :] == ["projects", "docs", "scripts", "generators"]


@allure.story("Invoke")
@allure.title("Unmatched quote in --args is a structured invalid_params refusal")
def test_invoke_wrapper_malformed_args_invalid_params(
    minimal_workspace: Path,
) -> None:
    result = invoke_capability(minimal_workspace, "classify-file", args="'")
    assert result.executed is False
    assert result.exit_code == 2
    assert result.refusal_code == REFUSAL_INVALID_PARAMS
    assert "cannot parse --args" in result.refusal_reason


@allure.story("Invoke")
@allure.title("Wrapper-covered route keeps its fixed command args on invoke")
def test_invoke_wrapper_route_preserves_fixed_args(
    minimal_workspace: Path,
) -> None:
    _script(
        minimal_workspace,
        "scripts/meta-sync-check.py",
        "import json, sys\n"
        "print(json.dumps({'ok': True, 'args': sys.argv[1:]}))\n",
    )
    overlay = minimal_workspace / "workspace-routes.yaml"
    data = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    data["routes"].append(
        {
            "id": "python-meta-sync-args",
            "target": "python",
            "read_only": True,
            "patterns": ["meta sync args"],
            "command": "python scripts/meta-sync-check.py --audit-fixed-arg",
        }
    )
    overlay.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    cap = _ops(collect_capabilities(minimal_workspace))["python-meta-sync-args"]
    with allure.step("advertised argv already carries the fixed arg"):
        assert cap.readiness == READY
        assert cap.invocable
        assert "--audit-fixed-arg" in cap.argv
    result = invoke_capability(minimal_workspace, "python-meta-sync-args")
    with allure.step("the executed argv matches the advertised contract"):
        assert result.executed is True
        assert json.loads(result.output)["args"] == ["--audit-fixed-arg"]


@allure.story("Invoke")
@allure.title("Wrapper --args is confined by realpath — bare symlink outside refused")
def test_invoke_wrapper_arg_symlink_confinement(
    minimal_workspace: Path,
) -> None:
    """A bare-token arg skips the lexical path check — the only gate left is
    realpath containment, so a symlink pointing outside the workspace must
    refuse as a structured 'symlink' refusal, not execute."""
    script = minimal_workspace / "scripts" / "ollama" / "classify-file.sh"
    script.write_text('#!/bin/sh\ncat "$1"\n', encoding="utf-8")
    script.chmod(0o755)
    outside = minimal_workspace.parent / "outside-secret.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    (minimal_workspace / "alias").symlink_to(outside)

    for arg in ("alias", "./alias", "alias/../alias"):
        result = invoke_capability(
            minimal_workspace, "classify-file", args=arg, log=False
        )
        with allure.step(f"{arg!r}: refused before exec, nothing read"):
            assert result.executed is False
            assert result.exit_code == 1
            assert "escapes workspace" in result.refusal_reason

    inside = minimal_workspace / "docs" / "real.txt"
    inside.write_text("INSIDE\n", encoding="utf-8")
    (minimal_workspace / "inside-alias").symlink_to(inside)
    result = invoke_capability(
        minimal_workspace, "classify-file", args="inside-alias", log=False
    )
    with allure.step("symlink resolving inside the workspace stays invocable"):
        assert result.executed is True
        assert "INSIDE" in result.output


@allure.story("Pipeline")
@allure.title("Route id outside PIPELINE_AUTO_RUN parses and refuses with reason")
def test_pipeline_route_step_refusal(minimal_workspace: Path) -> None:
    with allure.step("parse no longer crashes on a route id"):
        steps = parse_pipeline("python-meta-sync-check")
        assert len(steps) == 1
        assert steps[0].step_id == "python-meta-sync-check"
        assert steps[0].tier == "python"
        assert steps[0].argv is None  # never auto-runnable

    with allure.step("execute → structured skip with readiness + invoke hint"):
        result = run_pipeline(
            "python-meta-sync-check", minimal_workspace, execute=True, log=False
        )
        sr = result.steps[0]
        assert sr.executed is False
        assert sr.ok is False
        assert "not in pipeline auto-run allowlist" in sr.output
        assert "route readiness: ready" in sr.output
        assert "capabilities invoke python-meta-sync-check" in sr.output

    with allure.step("unapproved route step reports its readiness class"):
        _script(minimal_workspace, "scripts/git-recent.py", "print('x')\n")
        result = run_pipeline(
            "python-git-recent", minimal_workspace, execute=True, log=False
        )
        assert "route readiness: not_approved" in result.steps[0].output

    with allure.step("dry-run keeps the plan view, no refusal needed"):
        result = run_pipeline(
            "python-meta-sync-check", minimal_workspace, execute=False, log=False
        )
        assert "(dry-run) python scripts/meta-sync-check.py" in result.steps[0].output

    with allure.step("truly unknown ids still fail fast with a clear message"):
        with pytest.raises(ValueError, match="Unknown step 'nope-nope'"):
            parse_pipeline("nope-nope")


def _run_cli(workspace: Path, trust_home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GREEDY_TOKEN_ROOT": str(workspace),
        "GREEDY_TOKEN_HOME": str(trust_home),
        "GREEDY_TOKEN_LOG": str(trust_home / "usage.jsonl"),
        "PYTHONUTF8": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "greedy_token", *args],
        capture_output=True,
        encoding="utf-8",
        env=env,
    )


@allure.story("CLI")
@allure.title("capabilities --json emits parseable JSON with every derived op")
def test_cli_capabilities_json(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    proc = _run_cli(minimal_workspace, tmp_path, "capabilities", "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["summary"]["ops"] == len(payload["ops"])
    assert payload["summary"]["invocable"] >= 1
    by_id = {op["id"]: op for op in payload["ops"]}
    assert by_id["python-meta-sync-check"]["readiness"] == "ready"
    assert by_id["python-meta-sync-check"]["invocable"] is True
    assert by_id["python-google-sheets"]["readiness"] == "write_not_invocable"
    assert by_id["python-auth-storage-probe"]["readiness"] == "disabled_or_shadow"


@allure.story("CLI")
@allure.title("capabilities invoke: ready op runs, unapproved op refuses nonzero")
def test_cli_invoke(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    proc = _run_cli(
        minimal_workspace,
        tmp_path,
        "--no-log",
        "capabilities",
        "invoke",
        "python-meta-sync-check",
    )
    assert proc.returncode == 0, proc.stderr
    assert "meta-sync-check-ok" in proc.stdout
    assert "gate=accepted/accepted" in proc.stdout

    marker = minimal_workspace / "cli-marker.txt"
    _script(
        minimal_workspace,
        "scripts/git-recent.py",
        f"open({str(marker)!r}, 'w').write('ran')\n",
    )
    proc = _run_cli(
        minimal_workspace,
        tmp_path,
        "--no-log",
        "capabilities",
        "invoke",
        "python-git-recent",
    )
    assert proc.returncode == 1
    assert "Refused: python-git-recent [not_approved]" in proc.stderr
    assert not marker.exists()

    proc = _run_cli(
        minimal_workspace,
        tmp_path,
        "--no-log",
        "capabilities",
        "invoke",
        "python-git-recent",
        "--json",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["refusal_code"] == "not_approved"
    assert payload["executed"] is False


@allure.story("CLI")
@allure.title("capabilities show inspects one op; unknown id exits 2")
def test_cli_show(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    proc = _run_cli(
        minimal_workspace,
        tmp_path,
        "capabilities",
        "show",
        "python-meta-sync-check",
        "--json",
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["id"] == "python-meta-sync-check"
    assert payload["argv"] == ["python", "scripts/meta-sync-check.py"] or payload[
        "argv"
    ][1] == "scripts/meta-sync-check.py"

    proc = _run_cli(
        minimal_workspace, tmp_path, "capabilities", "show", "ghost-op"
    )
    assert proc.returncode == 2
    assert "Unknown capability" in proc.stderr
