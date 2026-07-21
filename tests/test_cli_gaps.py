"""Public-contract tests for CLI handlers added in v0.6 (fail_under=100)."""

from __future__ import annotations

import io
import json
import sys
from argparse import Namespace
from pathlib import Path

import allure
import pytest

import greedy_token.cli as cli
import greedy_token.hub as hub_pkg
from greedy_token.llm_invoke import InvokeResult
from greedy_token.resource_probe import BenchmarkResult, DoctorReport, HardwareProfile

pytestmark = [
    allure.epic("CLI"),
    allure.parent_suite("CLI"),
    allure.feature("CLI v0.6 handlers"),
    allure.suite("CLI gaps"),
]


def _ns(**kwargs) -> Namespace:
    return Namespace(no_log=True, **kwargs)


@allure.title("_parse_tags parses key=value pairs and skips junk")
def test_parse_tags() -> None:
    assert cli._parse_tags("project=tms, step=classify , bad, =noval,k=") == {
        "project": "tms",
        "step": "classify",
        "": "noval",
        "k": "",
    }


@allure.title("cmd_scripts lint runs crystallize lint report")
def test_cmd_scripts_lint(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_scripts(_ns(list=False, run=None, args="lint", execute=False))
    out = capsys.readouterr().out
    # exit code must mirror the printed report, not merely "did not crash"
    assert code in (0, 1)
    if code == 0:
        assert "scripts lint OK" in out
    else:
        assert "FAILED" in out


@allure.title("cmd_config --init --preset warns when url/model/provider also passed")
def test_cmd_config_preset_ignores_flags(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg)
    code = cli.cmd_config(
        _ns(init=True, preset="local-ollama", url="http://x", model=None, provider=None,
            force=True, list_presets=False, export=False)
    )
    err = capsys.readouterr().err
    assert code == 0
    assert "ignores" in err


@allure.title("cmd_config --init bad preset returns error")
def test_cmd_config_init_bad_preset(tmp_path: Path, minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg)
    code = cli.cmd_config(
        _ns(init=True, preset="no-such-preset-xyz", url=None, model=None, provider=None,
            force=True, list_presets=False, export=False)
    )
    err = capsys.readouterr().err
    assert code == 1
    assert err.strip()


def _invoke_ns(**kw) -> Namespace:
    base = dict(
        profile="p", system="", user="", system_file=None, user_file=None,
        tags="", json=False, allow_expensive=False, no_escalate=False,
    )
    base.update(kw)
    return _ns(**base)


@allure.title("cmd_llm_invoke: files, stdin, missing user, json, text, error")
def test_cmd_llm_invoke(minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    result = InvokeResult(
        text="answer", model_id="big", profile="p", tier_billing="cheap",
        escalated_from="fast", eval_tokens=5, cost_usd=0.0, duration_ms=1, attempts=["fast", "big"],
    )
    monkeypatch.setattr("greedy_token.llm_invoke.invoke_profile", lambda *a, **k: result)

    sys_file = tmp_path / "sys.txt"
    sys_file.write_text("system prompt", encoding="utf-8")
    user_file = tmp_path / "user.txt"
    user_file.write_text("user prompt", encoding="utf-8")

    # text mode + escalation note (system/user from files)
    code = cli.cmd_llm_invoke(_invoke_ns(system_file=str(sys_file), user_file=str(user_file)))
    out, err = capsys.readouterr()
    assert code == 0 and "answer" in out and "escalated" in err

    # json mode, user via stdin
    monkeypatch.setattr(sys, "stdin", io.StringIO("stdin user text"))
    code_j = cli.cmd_llm_invoke(_invoke_ns(json=True))
    out_j = capsys.readouterr().out
    assert code_j == 0 and json.loads(out_j)["model_id"] == "big"

    # missing user → exit 2
    monkeypatch.setattr(sys, "stdin", io.StringIO("   "))
    assert cli.cmd_llm_invoke(_invoke_ns()) == 2

    # text mode without escalation (escalated_from falsy)
    plain = InvokeResult(text="plain", model_id="fast", profile="p", tier_billing="cheap")
    monkeypatch.setattr("greedy_token.llm_invoke.invoke_profile", lambda *a, **k: plain)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    code_p = cli.cmd_llm_invoke(_invoke_ns())
    out_p, err_p = capsys.readouterr()
    assert code_p == 0 and "plain" in out_p and "escalated" not in err_p

    # RuntimeError from invoke → exit 1
    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr("greedy_token.llm_invoke.invoke_profile", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("ask something"))
    assert cli.cmd_llm_invoke(_invoke_ns()) == 1

    # missing prompt file → exit 2 with a clean message (no traceback)
    missing = tmp_path / "does-not-exist.txt"
    code_missing = cli.cmd_llm_invoke(_invoke_ns(user_file=str(missing)))
    err_missing = capsys.readouterr().err
    assert code_missing == 2
    assert "cannot read prompt file" in err_missing


@allure.title("cmd_llm_list prints registry and models")
def test_cmd_llm_list(minimal_workspace: Path, capsys) -> None:
    code = cli.cmd_llm_list(_ns())
    out = capsys.readouterr().out
    assert code == 0
    assert "policy:" in out and "models:" in out


def _report() -> DoctorReport:
    return DoctorReport(
        hardware=HardwareProfile("mid_vram", 16, 8, 12, 8, "gpu", "Linux"),
        ollama_available=True, ollama_url="http://o", installed=[], configured_model="qwen2.5:7b",
        recommended=["qwen2.5-coder:7b"], deprecated_installed=[], avoid_installed=[],
        benchmark=BenchmarkResult("m", 10, 5, True), paid_recommendations=["paid-a"],
    )


@allure.title("cmd_doctor: text, json, apply ok, apply error, no workspace")
def test_cmd_doctor(minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr("greedy_token.resource_probe.run_doctor", lambda **k: _report())
    monkeypatch.setattr("greedy_token.resource_probe.format_doctor_report", lambda r, include_paid=False: "DOCTOR TEXT")

    code = cli.cmd_doctor(_ns(apply=False, force=False, benchmark=True, paid=True, json=False))
    assert code == 0 and "DOCTOR TEXT" in capsys.readouterr().out

    code_j = cli.cmd_doctor(_ns(apply=False, force=False, benchmark=True, paid=True, json=True))
    data = json.loads(capsys.readouterr().out)
    assert code_j == 0 and data["ollama_available"] is True and "benchmark" in data and "paid_recommendations" in data

    # json without benchmark and without paid (drops both keys)
    report_min = _report()
    report_min.benchmark = None
    monkeypatch.setattr("greedy_token.resource_probe.run_doctor", lambda **k: report_min)
    code_jm = cli.cmd_doctor(_ns(apply=False, force=False, benchmark=False, paid=False, json=True))
    data_m = json.loads(capsys.readouterr().out)
    assert code_jm == 0 and "benchmark" not in data_m and "paid_recommendations" not in data_m

    # apply success
    monkeypatch.setattr("greedy_token.resource_probe.apply_doctor_config", lambda force: Path("/tmp/cfg.yaml"))
    code_a = cli.cmd_doctor(_ns(apply=True, force=True, benchmark=False, paid=False, json=False))
    assert code_a == 0 and "Updated" in capsys.readouterr().out

    # apply error
    def boom(force):
        raise ValueError("no rec")

    monkeypatch.setattr("greedy_token.resource_probe.apply_doctor_config", boom)
    code_e = cli.cmd_doctor(_ns(apply=True, force=False, benchmark=False, paid=False, json=False))
    assert code_e == 1 and "no rec" in capsys.readouterr().err

    # no workspace root tolerated
    monkeypatch.setattr(cli, "find_workspace_root", lambda: (_ for _ in ()).throw(SystemExit(1)))
    code_nw = cli.cmd_doctor(_ns(apply=False, force=False, benchmark=False, paid=False, json=False))
    assert code_nw == 0


@allure.title("cmd_budget tolerates missing workspace root")
def test_cmd_budget_no_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setattr(cli, "find_workspace_root", lambda: (_ for _ in ()).throw(SystemExit(1)))
    code = cli.cmd_budget(_ns(json=False, verbose=False))
    assert code == 0 and capsys.readouterr().out.strip()

    code_j = cli.cmd_budget(_ns(json=True, verbose=False))
    assert code_j == 0 and "metered_spent_usd" in json.loads(capsys.readouterr().out)


@allure.title("cmd_watch delegates to watch_events")
def test_cmd_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_watch(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "watch_events", fake_watch)
    assert cli.cmd_watch(_ns(once=True, from_start=True, json=True)) == 0
    assert captured == {"follow": False, "from_start": True, "json_out": True}


@allure.title("cmd_override logs event in text and json modes")
def test_cmd_override(minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))
    base = dict(
        crystal_id="script-foo", task="retry task", selected_tier="cursor", previous_tier="python",
        reason="manual", prior_usage_ts=None, window_sec=900, tags="project=tms", json=False,
    )
    code = cli.cmd_override(_ns(**base))
    out = capsys.readouterr().out
    assert code == 0 and "script_override logged" in out

    base["json"] = True
    code_j = cli.cmd_override(_ns(**base))
    out_j = capsys.readouterr().out
    assert code_j == 0 and json.loads(out_j)


@allure.title("cmd_hub_serve delegates to hub.serve")
def test_cmd_hub_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setattr(hub_pkg, "serve", lambda **kwargs: captured.update(kwargs))
    assert cli.cmd_hub_serve(_ns(host="0.0.0.0", port=9999)) == 0
    assert captured == {"host": "0.0.0.0", "port": 9999}
