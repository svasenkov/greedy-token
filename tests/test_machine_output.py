from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

import allure
from greedy_token.budget import wrap_mcp_response
from greedy_token.tokens import count_tokens
from greedy_token.tool_output import (
    MACHINE_OUTPUT_MAX_BYTES,
    MACHINE_OUTPUT_MAX_TOKENS,
    TOOL_OUTPUT_BYTE_CAP,
    cap_tool_output,
    format_machine_output,
    shrink_json_payload,
)
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Tool output"),
    allure.parent_suite("Tool output"),
    allure.feature("Machine output"),
    allure.suite("Machine output"),
]


def _envelope_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


@allure.story("Byte cap")
@allure.title("cap_tool_output caps a single 100 KB line at the byte cap")
def test_cap_tool_output_single_100kb_line() -> None:
    raw = "x" * (100 * 1024)
    with allure.step("Cap a 100 KB single-line output"):
        out = cap_tool_output(raw)
        attach_text("capped output tail", out[-200:])
    with allure.step("Verify the byte cap bound and the truncation marker"):
        assert _envelope_bytes(out) <= TOOL_OUTPUT_BYTE_CAP
        assert "truncated" in out


@allure.story("Byte cap")
@allure.title("cap_tool_output byte cap never splits a UTF-8 codepoint")
def test_cap_tool_output_utf8_boundary() -> None:
    # 2-byte chars: a byte cut can land inside a codepoint.
    raw = "ы" * (TOOL_OUTPUT_BYTE_CAP)  # 2×cap bytes, single line
    with allure.step("Cap multi-byte single-line output"):
        out = cap_tool_output(raw)
    with allure.step("Verify valid UTF-8 within the cap"):
        assert _envelope_bytes(out) <= TOOL_OUTPUT_BYTE_CAP
        assert out.encode("utf-8").decode("utf-8") == out
        body = out.split("…")[0]
        assert all(ch == "ы" for ch in body.strip())


@allure.story("Byte cap")
@allure.title("Line cap and byte cap combine on multi-line floods")
def test_cap_tool_output_line_and_byte_caps() -> None:
    raw = "\n".join(f"line {i} {'y' * 4000}" for i in range(40))
    out = cap_tool_output(raw, limit=30)
    assert _envelope_bytes(out) <= TOOL_OUTPUT_BYTE_CAP
    assert "truncated" in out


@allure.story("Machine envelope")
@allure.title("machine style returns a capped JSON envelope instead of body+footer")
def test_wrap_mcp_response_machine_envelope_caps_100kb(
    minimal_workspace: Path,
) -> None:
    body = "z" * (100 * 1024)
    with allure.step("Wrap a 100 KB body in machine mode"):
        out = wrap_mcp_response(
            body,
            task="probe",
            tier="tool",
            est_tokens=0,
            route_id="probe",
            root=minimal_workspace,
            log=False,
            style="machine",
        )
        attach_text("envelope head", out[:400])
    with allure.step("Verify a parseable capped envelope without the human footer"):
        doc = json.loads(out)
        assert _envelope_bytes(out) <= MACHINE_OUTPUT_MAX_BYTES
        assert count_tokens(out).tokens <= MACHINE_OUTPUT_MAX_TOKENS
        assert doc["cap_bytes"] == MACHINE_OUTPUT_MAX_BYTES
        assert doc["cap_tokens"] == MACHINE_OUTPUT_MAX_TOKENS
        assert doc["payload_bytes"] == _envelope_bytes(out)
        assert doc["truncated"] is True
        assert doc["ok"] is True
        assert "Greedy token" not in out


@allure.story("Machine envelope")
@allure.title("Structural JSON truncation keeps verdict and count fields")
def test_shrink_json_payload_preserves_verdict_and_count() -> None:
    payload = {
        "ok": False,
        "count": 5000,
        "issues_total": 5000,
        "issues": [{"line": i, "rule": "missing"} for i in range(5000)],
    }
    with allure.step("Shrink a large JSON payload to the cap"):
        shrunk = shrink_json_payload(
            payload,
            max_bytes=4096,
            max_tokens=MACHINE_OUTPUT_MAX_TOKENS,
        )
        attach_text("shrunk keys", json.dumps(sorted(shrunk.keys())))
    with allure.step("Verify scalars survive and list tail is counted"):
        assert shrunk["ok"] is False
        assert shrunk["count"] == 5000
        assert shrunk["issues_total"] == 5000
        assert shrunk["truncated"] is True
        assert len(shrunk["issues"]) < 5000
        assert shrunk.get("issues_dropped", 0) + len(shrunk["issues"]) == 5000
        assert _envelope_bytes(json.dumps(shrunk, ensure_ascii=False)) <= 4096


@allure.story("Machine envelope")
@allure.title("A JSON contract string inside a field shrinks structurally")
def test_shrink_json_payload_inner_contract_string() -> None:
    inner = {
        "ok": False,
        "count": 2000,
        "issues": [{"i": i} for i in range(2000)],
        "issues_total": 2000,
        "truncated": False,
    }
    payload = {
        "op_id": "python-check",
        "executed": True,
        "exit_code": 1,
        "result_status": "produced",
        "output": json.dumps(inner, ensure_ascii=False),
    }
    shrunk = shrink_json_payload(payload, max_bytes=4096)
    assert shrunk["result_status"] == "produced"
    assert shrunk["exit_code"] == 1
    inner_out = json.loads(shrunk["output"])
    assert inner_out["ok"] is False
    assert inner_out["count"] == 2000
    assert inner_out["truncated"] is True


@allure.story("Machine envelope")
@allure.title("Negative terminal verdict is not a technical failure")
def test_machine_envelope_negative_verdict_vs_error(
    minimal_workspace: Path,
) -> None:
    with allure.step("Executed negative verdict (produced contract failure)"):
        out = wrap_mcp_response(
            "scan finished: 3 violations",
            task="probe",
            tier="python",
            est_tokens=0,
            root=minimal_workspace,
            log=False,
            outcome="failure",
            result_status="produced",
            executed=True,
            style="machine",
        )
        doc = json.loads(out)
        attach_text("negative verdict", out)
        assert doc["ok"] is False
        assert doc["outcome"] == "failure"
        assert doc["result_status"] == "produced"
        assert "error" not in doc
    with allure.step("Technical failure / refusal carries an error object"):
        out2 = wrap_mcp_response(
            "",
            task="probe",
            tier="python",
            est_tokens=0,
            root=minimal_workspace,
            log=False,
            outcome="failure",
            executed=False,
            style="machine",
            machine_payload={
                "op_id": "python-git-recent",
                "invocable": False,
                "executed": False,
                "exit_code": 1,
            },
            machine_error={"code": "not_approved", "message": "no trust entry"},
        )
        doc2 = json.loads(out2)
        attach_text("refusal", out2)
        assert doc2["ok"] is False
        assert doc2["error"]["code"] == "not_approved"


@allure.story("Machine envelope")
@allure.title("Human output is unchanged: footer styles keep their shape")
def test_human_output_preserved(minimal_workspace: Path) -> None:
    for style in (None, "compact"):
        out = wrap_mcp_response(
            "body text",
            task="probe",
            tier="tool",
            est_tokens=0,
            route_id="probe",
            root=minimal_workspace,
            log=False,
            style=style,
        )
        assert "Greedy token" in out
        assert "body text" in out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)


@allure.story("Machine envelope")
@allure.title("MCP search in machine mode: JSON envelope, count kept, no footer")
def test_mcp_search_machine_mode(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_FOOTER_STYLE", "machine")
    from greedy_token.mcp import greedy_token_search

    out = greedy_token_search("baseUrl", "sample.js")
    doc = json.loads(out)
    attach_text("machine search", out)
    assert doc["ok"] is True
    assert doc["count"] >= 1
    assert "baseUrl" in doc["output"]
    assert doc["result_status"] == "produced"
    assert "Greedy token" not in out
    assert _envelope_bytes(out) <= doc["cap_bytes"]


@allure.story("Machine envelope")
@allure.title("MCP invoke refusal is an error object, not a verdict failure")
def test_mcp_invoke_machine_refusal(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_FOOTER_STYLE", "machine")
    (minimal_workspace / "scripts" / "git-recent.py").write_text(
        "print('x')\n", encoding="utf-8"
    )
    from greedy_token.mcp import greedy_token_invoke

    out = greedy_token_invoke("python-git-recent")
    doc = json.loads(out)
    attach_text("machine refusal", out)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "not_approved"
    assert doc["invocable"] is False
    assert doc["executed"] is False
    # The refusal verdict is classified before the logging branch —
    # invoke wraps with log=False and still reports "refused".
    assert doc["outcome"] == "refused"


@allure.story("Machine envelope")
@allure.title("MCP invoke of a failing contract script keeps the negative verdict")
def test_mcp_invoke_machine_negative_verdict(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_FOOTER_STYLE", "machine")
    from greedy_token.mcp import greedy_token_invoke
    from greedy_token.trust import approve_script

    routes = minimal_workspace / "workspace-routes.yaml"
    head, sep, tail = routes.read_text(encoding="utf-8").partition(
        "\ncursor_fallback:"
    )
    assert sep
    routes.write_text(
        head
        + "\n  - id: python-fail-check\n"
        + "    target: python\n"
        + "    read_only: true\n"
        + "    patterns: [fail check fixture]\n"
        + '    command: python scripts/fail-check.py\n'
        + sep
        + tail,
        encoding="utf-8",
    )
    script = minimal_workspace / "scripts" / "fail-check.py"
    script.write_text(
        "import json, sys\n"
        "print(json.dumps({'ok': False, 'issues': [{'k': 1}]}))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    approve_script(minimal_workspace, "scripts/fail-check.py")

    out = greedy_token_invoke("python-fail-check")
    doc = json.loads(out)
    attach_text("machine negative verdict", out)
    assert doc["ok"] is False
    assert doc["executed"] is True
    assert doc["exit_code"] == 1
    assert doc["result_status"] == "produced"
    assert doc["outcome"] == "failure"
    assert "error" not in doc
    assert json.loads(doc["output"])["ok"] is False


@allure.story("Run planning")
@allure.title("run --execute plans once: execute_task reuses the cmd_run plan")
def test_run_execute_single_planning(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import greedy_token.cli as cli
    import greedy_token.executors as executors

    calls = {"route": 0, "plan": 0}
    orig_route = executors.route_task
    orig_plan = executors.plan_run

    def count_route(*a, **k):
        calls["route"] += 1
        return orig_route(*a, **k)

    def count_plan(*a, **k):
        calls["plan"] += 1
        return orig_plan(*a, **k)

    monkeypatch.setattr(executors, "route_task", count_route)
    monkeypatch.setattr(executors, "plan_run", count_plan)
    monkeypatch.setattr(cli, "route_task", count_route)
    monkeypatch.setattr(cli, "plan_run", count_plan)

    args = cli.build_parser().parse_args(
        ["--no-log", "run", "find baseUrl in sample.js", "--execute"]
    )
    code = args.func(args)
    attach_text("call counts", json.dumps(calls))
    assert code == 0
    assert calls["route"] == 1
    assert calls["plan"] == 1


@allure.story("Machine envelope")
@allure.title("capabilities invoke --json output stays inside the cap")
def test_cli_invoke_json_capped(
    minimal_workspace: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import greedy_token.cli as cli
    from greedy_token.trust import approve_script

    routes = minimal_workspace / "workspace-routes.yaml"
    head, sep, tail = routes.read_text(encoding="utf-8").partition(
        "\ncursor_fallback:"
    )
    assert sep
    routes.write_text(
        head
        + "\n  - id: python-big-check\n"
        + "    target: python\n"
        + "    read_only: true\n"
        + "    patterns: [big check fixture]\n"
        + '    command: python scripts/big-check.py\n'
        + sep
        + tail,
        encoding="utf-8",
    )
    script = minimal_workspace / "scripts" / "big-check.py"
    script.write_text(
        "print('x' * 60000)\nprint('{\"ok\": true}')\n",
        encoding="utf-8",
    )
    approve_script(minimal_workspace, "scripts/big-check.py")

    args = cli.build_parser().parse_args(
        ["--no-log", "capabilities", "invoke", "python-big-check", "--json"]
    )
    assert args.func(args) == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    attach_text("capped invoke --json", out[:400])
    assert _envelope_bytes(out) <= MACHINE_OUTPUT_MAX_BYTES
    assert doc["executed"] is True
    assert doc["exit_code"] == 0
    assert doc["truncated"] is True


def _flat_contract() -> dict:
    """Reviewer repro: 5000 scalar fields + verdict/count contract keys."""
    return {
        "ok": False,
        "count": 5000,
        "issues_total": 5000,
        **{f"field_{i:04d}": i for i in range(5000)},
    }


@allure.story("Machine envelope")
@allure.title("Flat scalar payload is shrunk inside the declared default caps")
def test_shrink_flat_scalar_payload_enforces_cap() -> None:
    flat = _flat_contract()
    with allure.step("Shrink a 5000-field flat dict"):
        shrunk = shrink_json_payload(flat)
        emitted = json.dumps(shrunk, ensure_ascii=False)
        attach_text("shrunk doc head", emitted[:300])
    with allure.step("Emitted form respects both caps; contract keys survive"):
        assert len(emitted.encode("utf-8")) <= MACHINE_OUTPUT_MAX_BYTES
        assert count_tokens(emitted).tokens <= MACHINE_OUTPUT_MAX_TOKENS
        assert shrunk["ok"] is False
        assert shrunk["count"] == 5000
        assert shrunk["issues_total"] == 5000
        assert shrunk["truncated"] is True
        assert shrunk.get("dropped_keys", 0) >= 1
    with allure.step("Payload path of format_machine_output is equally capped"):
        out = format_machine_output(payload=flat)
        doc = json.loads(out)
        assert len(out.encode("utf-8")) <= MACHINE_OUTPUT_MAX_BYTES
        assert count_tokens(out).tokens <= MACHINE_OUTPUT_MAX_TOKENS
        assert doc["payload_bytes"] == len(out.encode("utf-8"))
        assert doc["ok"] is False
        assert doc["count"] == 5000
        assert doc["truncated"] is True


@allure.story("Machine envelope")
@allure.title("A big JSON contract body is never emitted as cut JSON text")
def test_machine_wrap_big_json_body_stays_valid(
    minimal_workspace: Path,
) -> None:
    flat = _flat_contract()
    with allure.step("Wrap a ~100KB valid JSON body in machine mode"):
        out = wrap_mcp_response(
            json.dumps(flat, ensure_ascii=False),
            task="probe",
            tier="python",
            est_tokens=0,
            root=minimal_workspace,
            log=False,
            outcome="failure",
            result_status="produced",
            executed=True,
            style="machine",
        )
        attach_text("envelope head", out[:300])
    with allure.step("Outer and inner JSON both parse; contract survives"):
        doc = json.loads(out)
        inner = json.loads(doc["output"])
        assert inner["ok"] is False
        assert inner["count"] == 5000
        assert inner["issues_total"] == 5000
        assert inner["truncated"] is True
        assert len(out.encode("utf-8")) <= doc["cap_bytes"]
        assert count_tokens(out).tokens <= doc["cap_tokens"]
        assert doc["truncated"] is True


@allure.story("Machine envelope")
@allure.title("Unsupported cap is rejected explicitly — never emitted oversize")
def test_machine_output_tiny_cap_rejected() -> None:
    with pytest.raises(ValueError):
        format_machine_output("body", max_bytes=100)


@allure.story("Machine envelope")
@allure.title("Caps at the final-accounting floor never emit an oversize envelope")
def test_machine_output_floor_edge_caps_bounded() -> None:
    for mb in (151, 152, 153):
        try:
            out = format_machine_output("x" * 200, max_bytes=mb)
        except ValueError:
            continue
        doc = json.loads(out)
        assert len(out.encode("utf-8")) <= mb
        assert count_tokens(out).tokens <= doc["cap_tokens"]
        assert doc["payload_bytes"] == len(out.encode("utf-8"))


@allure.story("Machine envelope")
@allure.title("Protected verdict values are immutable; inner contract survives or refuses")
def test_machine_output_protected_values_immutable() -> None:
    contract = {
        "ok": False,
        "count": 10,
        "issues_total": 10,
        "issues": [{"line": i, "rule": "missing"} for i in range(10)],
        "truncated": False,
    }
    try:
        out = format_machine_output(
            json.dumps(contract),
            ok=False,
            outcome="failure",
            result_status="produced",
            executed=True,
            max_bytes=256,
        )
    except ValueError:
        return  # explicit refusal of an unrepresentable contract is valid
    doc = json.loads(out)
    assert len(out.encode("utf-8")) <= 256
    # protected contract values survive verbatim — never emptied or mutated
    assert doc["ok"] is False
    assert doc["outcome"] == "failure"
    assert doc["result_status"] == "produced"
    assert doc["executed"] is True
    output = doc.get("output")
    if output is None:
        # inner contract could not be represented: explicit refusal required
        assert doc["ok"] is False
        assert doc["error"]["code"]
        return
    inner = json.loads(output)  # never cut JSON text
    assert inner["ok"] is False
    assert inner["count"] == 10
    assert inner["issues_total"] == 10


@allure.story("Machine envelope")
@allure.title("Unrepresentable protected skeleton raises instead of oversize")
def test_shrink_json_payload_unrepresentable_skeleton_raises() -> None:
    protected = {
        "ok": False,
        "count": 10,
        "issues_total": 10,
        **{f"bucket_{i}_total": i for i in range(40)},
    }
    with pytest.raises(ValueError):
        shrink_json_payload(protected, max_bytes=256)


@allure.story("CLI bounded refusal")
@allure.title("invoke --json refusal never echoes an unbounded identity")
def test_cli_invoke_json_refusal_is_bounded(
    minimal_workspace: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import greedy_token.capabilities_invoke as capabilities_invoke
    import greedy_token.cli as cli
    from greedy_token.capabilities_invoke import InvocationResult

    calls = {"invoke": 0}
    big_op_id = "SYNTHETIC_OP_" + "x" * 60000

    def fake_invoke(root, op_id, *, args="", query="", log=True):
        calls["invoke"] += 1
        return InvocationResult(
            op_id=big_op_id,
            tier="python",
            invocable=False,
            executed=False,
            exit_code=2,
            output="",
            refusal_code="unknown_operation",
            refusal_reason="no such operation",
        )

    monkeypatch.setattr(
        capabilities_invoke, "invoke_capability", fake_invoke
    )
    ns = cli.build_parser().parse_args(
        ["--no-log", "capabilities", "invoke", big_op_id, "--json"]
    )
    with allure.step("Invoke --json with an unrepresentable op_id"):
        rc = ns.func(ns)
        out = capsys.readouterr().out
        attach_text("bounded refusal", out)
    doc = json.loads(out)
    assert len(out.encode("utf-8")) <= MACHINE_OUTPUT_MAX_BYTES
    assert count_tokens(out).tokens <= MACHINE_OUTPUT_MAX_TOKENS
    assert calls["invoke"] == 1
    assert doc["ok"] is False
    assert doc["error"]["code"] == "unrepresentable"
    assert doc.get("op_id") != big_op_id
    assert big_op_id not in out
    assert rc != 0


@allure.story("CLI bounded refusal")
@allure.title("invoke --json delivery refusal returns a technical nonzero rc")
def test_cli_invoke_json_delivery_refusal_nonzero_rc(
    minimal_workspace: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import greedy_token.capabilities_invoke as capabilities_invoke
    import greedy_token.cli as cli
    from greedy_token.capabilities_invoke import InvocationResult

    calls = {"invoke": 0}
    result = InvocationResult(
        op_id="python-ok",
        tier="python",
        invocable=True,
        executed=True,
        exit_code=0,
        output='{"ok": true}',
        result_status="produced",
        outcome="success",
    )

    def fake_invoke(root, op_id, *, args="", query="", log=True):
        calls["invoke"] += 1
        return result

    def fake_shrink(*a, **k):
        raise ValueError("unrepresentable")

    monkeypatch.setattr(
        capabilities_invoke, "invoke_capability", fake_invoke
    )
    monkeypatch.setattr(cli, "shrink_json_payload", fake_shrink)
    ns = cli.build_parser().parse_args(
        ["--no-log", "capabilities", "invoke", "python-ok", "--json"]
    )
    rc = ns.func(ns)
    out = capsys.readouterr().out
    attach_text("delivery refusal", out)
    doc = json.loads(out)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "unrepresentable"
    assert len(out.encode("utf-8")) <= MACHINE_OUTPUT_MAX_BYTES
    assert calls["invoke"] == 1
    assert rc != 0  # delivery refusal is a technical failure, not success


# --- Coverage gaps: utf8 prefix / byte-cap edges / structural shrinkers ---


@allure.title("_utf8_prefix: non-positive limit drops all, roomy limit keeps all")
def test_utf8_prefix_edges() -> None:
    from greedy_token import tool_output as tl

    assert tl._utf8_prefix("abc", 0) == ("", 3)
    assert tl._utf8_prefix("abc", -5) == ("", 3)
    assert tl._utf8_prefix("abc", 10) == ("abc", 0)


@allure.title("cap_tool_output: byte_cap<=0 disables the byte cap entirely")
def test_cap_tool_output_no_byte_cap() -> None:
    raw = "x" * (TOOL_OUTPUT_BYTE_CAP + 10)
    out = cap_tool_output(raw, limit=10, byte_cap=0)
    # Line cap still applies — byte cap is fully skipped.
    assert out.startswith("x" * 100)
    assert len(out.encode("utf-8")) > TOOL_OUTPUT_BYTE_CAP


@allure.title("cap_tool_output: byte_cap < 96 emits a bare prefix without the marker")
def test_cap_tool_output_tiny_byte_cap() -> None:
    out = cap_tool_output("y" * 500, limit=0, byte_cap=50)
    assert out == "y" * 50
    assert "truncated" not in out


@allure.title("_biggest_list: descends dict values, skips non-best lists, ignores scalars")
def test_biggest_list_traversal() -> None:
    from greedy_token import tool_output as tl

    doc = {
        "small": [1],
        "wrap": {"inner": {"big": ["x" * 40, "y" * 40]}},
        "mixed": [["a", "b"], "scalar"],
        "empty": [],
    }
    parent, key = tl._biggest_list(doc)
    assert parent is doc["wrap"]["inner"]
    assert key == "big"


@allure.title("_biggest_string: descends dict/list values and skips scalars")
def test_biggest_string_traversal() -> None:
    from greedy_token import tool_output as tl

    doc = {
        "n": 5,
        "wrap": {"deep": "x" * 90},
        "lst": [1, "s"],
        "small": "xy",
    }
    parent, key = tl._biggest_string(doc)
    assert parent is doc["wrap"]
    assert key == "deep"


@allure.title("_shrink_text: fitting text returns unchanged")
def test_shrink_text_fits_early() -> None:
    from greedy_token import tool_output as tl

    assert tl._shrink_text("short", 100) == "short"


@allure.title("_shrink_text: invalid JSON degrades to the marker path")
def test_shrink_text_invalid_json() -> None:
    from greedy_token import tool_output as tl

    out = tl._shrink_text("{not valid json at all, just text", 100)
    assert out == "{not valid json at all, just text"
    out = tl._shrink_text("{still not json" + "x" * 100, 20)
    assert out.endswith("…")


@allure.title("_shrink_text: contract JSON reduces to skeleton or empty")
def test_shrink_text_contract_skeleton() -> None:
    from greedy_token import tool_output as tl

    payload = json.dumps({"ok": True, "data": "x" * 400})
    out = tl._shrink_text(payload, 80)
    doc = json.loads(out)
    assert doc["ok"] is True
    assert doc["truncated"] is True

    out = tl._shrink_text(payload, 5)
    assert out == ""


@allure.title("_shrink_text: contract-free JSON degrades to marker, {} or empty")
def test_shrink_text_free_json() -> None:
    from greedy_token import tool_output as tl

    payload = json.dumps({"a": "x" * 300})
    assert tl._shrink_text(payload, 21) == '{"truncated": true}'
    assert tl._shrink_text(payload, 5) == "{}"
    assert tl._shrink_text(payload, 1) == ""


@allure.title("_shrink_text: JSON list shrinks to fit or becomes []")
def test_shrink_text_list_doc() -> None:
    from greedy_token import tool_output as tl

    payload = json.dumps([1, 2, 3])
    assert tl._shrink_text(payload, 10) == "[1, 2, 3]"
    big = json.dumps(["x" * 200] * 4)
    assert tl._shrink_text(big, 40) == "[]"
    assert tl._shrink_text(big, 1) == ""


@allure.title("_shrink_text: JSON string documents shrink their content")
def test_shrink_text_string_doc() -> None:
    from greedy_token import tool_output as tl

    out = tl._shrink_text('"' + "x" * 200 + '"', 30)
    assert out.endswith("…")
    out = tl._shrink_text('"unterminated string doc' + "x" * 100, 30)
    assert out.endswith("…")


@allure.title("_shrink_value: lists drop the tail, strings shrink, scalars pass")
def test_shrink_value_variants() -> None:
    from greedy_token import tool_output as tl

    shrunk = tl._shrink_value(["x" * 20] * 40, 100)
    assert tl._json_bytes(shrunk) <= 100
    assert tl._shrink_value("x" * 200, 30).endswith("…")
    assert tl._shrink_value(42, 1) == 42
    assert tl._shrink_value(None, 1) is None


@allure.title("_shrink_string_field: a stalled shrinker breaks on identical output")
def test_shrink_string_field_stalled_break(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import tool_output as tl

    # The shrinker returns an identical string twice: the accounting loop
    # must stop instead of spinning on no forward progress.
    monkeypatch.setattr(tl, "_shrink_text", lambda text, allowed: "x" * 20)
    parent = {"keykey7": "x" * 5000}
    doc_size = tl._json_bytes(parent)
    dropped = tl._shrink_string_field(
        parent, "keykey7", doc_size=doc_size, budget_bytes=32
    )
    assert parent["keykey7"] == "x" * 20
    assert dropped == doc_size - tl._json_bytes({"keykey7": "x" * 20})


@allure.title("_shrink_string_field: the 8-iteration bound stops endless regress")
def test_shrink_string_field_loop_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import tool_output as tl

    calls: list[str] = []
    monkeypatch.setattr(
        tl,
        "_shrink_text",
        lambda text, allowed: calls.append(text) or text + "!",
    )
    parent = {"keykey7": "x" * 500}
    dropped = tl._shrink_string_field(
        parent, "keykey7", doc_size=tl._json_bytes(parent), budget_bytes=10
    )
    assert len(calls) == 8
    assert dropped < 0  # the field grew; the bound stopped the loop anyway


@allure.title("_shrink_dict_in_place: zero-gain fields fall through to key dropping")
def test_shrink_dict_in_place_zero_gain_drops_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from greedy_token import tool_output as tl

    monkeypatch.setattr(tl, "_shrink_text", lambda text, allowed: text)
    doc = {"ok": True, "field": "x" * 400}
    tl._shrink_dict_in_place(doc, 100)
    assert "field" not in doc
    assert doc["dropped_keys"] >= 1
    assert doc["truncated"] is True


@allure.title("_shrink_dict_in_place: the 512-iteration bound caps pathological input")
def test_shrink_dict_in_place_loop_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import tool_output as tl

    monkeypatch.setattr(tl, "_tail_drop_count", lambda items, deficit: 0)
    doc = {"ok": True, "items": ["x" * 50] * 600}
    dropped = tl._shrink_dict_in_place(doc, 10)
    assert dropped == 512 * tl._json_bytes([])
    assert doc["items"] == ["x" * 50] * 600
    assert doc["items_dropped"] == 0


@allure.title("_strip_to_skeleton: the error code survives into the skeleton")
def test_strip_to_skeleton_error_code() -> None:
    from greedy_token import tool_output as tl

    doc = {
        "ok": False,
        "error": {"code": "denied", "detail": "x" * 100},
        "blob": "y" * 200,
    }
    skel = tl._strip_to_skeleton(doc)
    assert skel["ok"] is False
    assert skel["error"] == {"code": "denied"}
    assert "blob" not in skel
    assert skel["truncated"] is True


@allure.title("shrink_json_payload: stalled progress exhausts the loop and refuses")
def test_shrink_json_payload_loop_exhaust_and_unrepresentable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from greedy_token import tool_output as tl

    # Progress is reported but the doc never actually shrinks: the fixpoint
    # must terminate after 16 passes and fall back to the skeleton.
    monkeypatch.setattr(tl, "_shrink_dict_in_place", lambda doc, budget: 1)
    doc = tl.shrink_json_payload(
        {"ok": True, "data": "x" * 400}, max_bytes=200, max_tokens=0
    )
    assert doc["ok"] is True
    assert "data" not in doc
    assert doc["truncated"] is True

    # Even the protected skeleton exceeds the cap — the caller must refuse.
    with pytest.raises(ValueError, match="unrepresentable"):
        tl.shrink_json_payload(
            {
                "ok": False,
                "error": {"code": "x" * 300},
                "data": "y" * 400,
            },
            max_bytes=64,
            max_tokens=0,
        )


@allure.title("_inner_floor: invalid JSON, contract-free dict, list, fallback")
def test_inner_floor_variants() -> None:
    from greedy_token import tool_output as tl

    assert tl._inner_floor("{invalid json") == "{invalid json"
    assert tl._inner_floor('{"a": 1, "b": 2}') == '{"truncated": true}'
    assert tl._inner_floor("[1, 2, 3]") == "[]"
    assert tl._inner_floor("plain text body") == "…"


@allure.title("_emit_doc: a converged fitting doc serializes once counters settle")
def test_emit_doc_converged() -> None:
    from greedy_token import tool_output as tl

    raw = tl._emit_doc({"ok": True}, 10**6, 10**6)
    doc = json.loads(raw)
    assert doc["ok"] is True
    assert doc["payload_bytes"] == len(raw.encode("utf-8"))
    assert doc["payload_tokens"] == count_tokens(raw).tokens


@allure.title("_emit_doc: oscillating counters hit the loop bound and raise")
def test_emit_doc_loop_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import tool_output as tl
    from greedy_token.tokens import TokenEstimate

    # Every measurement reports a different token count, so the
    # payload_* accounting can never converge — the bound must fire.
    counter = itertools.count()
    monkeypatch.setattr(
        tl,
        "count_tokens",
        lambda raw: TokenEstimate(
            tokens=next(counter), chars=len(raw), method="fake"
        ),
    )
    with pytest.raises(ValueError, match="unrepresentable"):
        tl._emit_doc({"ok": True}, 10**6, 10**6)


@allure.title("format_machine_output: losing all content emits the refusal envelope")
def test_format_machine_output_content_lost_refusal() -> None:
    # The byte budget admits the mandatory floor but not the content — the
    # converged skeleton lost every content key, so the envelope is a
    # delivery refusal, not a success with silently vanished fields.
    out = format_machine_output(
        payload={
            "ok": True,
            "error": {"code": "x" * 20},
            "meta": {"blob": "y" * 200},
            "output": "x" * 20000,
        },
        max_bytes=220,
        max_tokens=2000,
    )
    doc = json.loads(out)
    assert doc["ok"] is False
    assert "error" in doc
    assert len(out.encode("utf-8")) <= 220


@allure.title("format_machine_output: a failing shrinker degrades to the refusal envelope")
def test_format_machine_output_shrink_valueerror_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from greedy_token import tool_output as tl

    # shrink_json_payload raising inside the convergence loop falls back to
    # the refusal floor — the gate already proved that floor representable.
    monkeypatch.setattr(
        tl,
        "shrink_json_payload",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("unrepresentable")),
    )
    out = format_machine_output(
        payload={"ok": True, "data": "x" * 2000},
        max_bytes=400,
        max_tokens=0,
    )
    doc = json.loads(out)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "unrepresentable"
    assert len(out.encode("utf-8")) <= 400


@allure.title("format_machine_output: a never-shrinking doc hits the loop bound")
def test_format_machine_output_loop_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import tool_output as tl

    monkeypatch.setattr(tl, "shrink_json_payload", lambda doc, **kw: doc)

    # Output survives into the mandatory floor as a marker → floor emitted.
    out = format_machine_output(
        payload={"ok": True, "output": "x" * 400},
        max_bytes=200,
        max_tokens=0,
    )
    doc = json.loads(out)
    assert doc["ok"] is True
    assert doc["output"] == "…"
    assert doc["truncated"] is True

    # Non-output content vanishes from the floor → refusal path.
    out = format_machine_output(
        payload={"ok": True, "data": "x" * 400},
        max_bytes=200,
        max_tokens=0,
    )
    doc = json.loads(out)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "unrepresentable"


@allure.title("filter_tool_output strips junk-path and blank lines")
def test_filter_tool_output_junk_and_blank_lines() -> None:
    from greedy_token.tool_output import filter_tool_output

    out = filter_tool_output(
        "src/a.py: hit\n"
        "x/.cursor/hooks/probe.py: noise\n"
        "   \n"
        "src/b.py: hit"
    )
    assert out == "src/a.py: hit\nsrc/b.py: hit"


@allure.title("cap_tool_output returns empty input unchanged")
def test_cap_tool_output_empty() -> None:
    assert cap_tool_output("") == ""


@allure.title("_shrink_text falls back to the contract skeleton when shrink overshoots")
def test_shrink_text_contract_skeleton_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from greedy_token import tool_output as tl

    # The shrinker produced a doc that still exceeds the field budget; the
    # protected contract (skeleton) is the last honest representation.
    monkeypatch.setattr(
        tl,
        "_shrink_value",
        lambda inner, budget: {"ok": True, "extra": "x" * 300},
    )
    text = json.dumps({"ok": True, "extra": "x" * 300})
    out = tl._shrink_text(text, 120)
    doc = json.loads(out)
    assert doc["ok"] is True
    assert "extra" not in doc
    assert doc["truncated"] is True


@allure.title("_strip_to_skeleton keeps an error dict that carries no code")
def test_strip_to_skeleton_error_without_code() -> None:
    from greedy_token.tool_output import _strip_to_skeleton

    skel = _strip_to_skeleton(
        {"ok": False, "error": {"msg": "m" * 60}, "big": "x" * 50}
    )
    assert skel["ok"] is False
    assert skel["error"] == {"msg": "m" * 60}
    assert "big" not in skel


@allure.title("_fits_emitted measures the emitted form without worst counters")
def test_fits_emitted_without_worst_counters() -> None:
    from greedy_token.tool_output import _fits_emitted

    assert _fits_emitted({"ok": True}, 1000, 0, worst_counters=False) is True
    assert _fits_emitted({"ok": True}, 5, 0, worst_counters=False) is False


@allure.title("format_machine_output emits kwargs metadata without a payload")
def test_format_machine_output_kwargs_metadata() -> None:
    out = format_machine_output(
        body="partial output",
        outcome="failure",
        result_status="refused",
        executed=False,
        operation_id="op-9",
        error={"code": "boom"},
        max_bytes=2000,
    )
    doc = json.loads(out)
    assert doc["ok"] is False
    assert doc["outcome"] == "failure"
    assert doc["result_status"] == "refused"
    assert doc["executed"] is False
    assert doc["operation_id"] == "op-9"
    assert doc["error"] == {"code": "boom"}
    assert doc["output"] == "partial output"
