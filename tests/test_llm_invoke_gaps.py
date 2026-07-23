"""Public-contract tests for llm_invoke escalation / expensive / logging (fail_under=100)."""

from __future__ import annotations

from pathlib import Path

import allure
import pytest
import yaml

from greedy_token import llm_invoke
from greedy_token.llm_invoke import (
    InvokeResult,
    _json_parse_fail,
    _output_weak,
    _should_escalate,
    invoke_profile,
    invoke_result_to_dict,
)
from greedy_token.spend_guard import SpendDecision

pytestmark = pytest.mark.unit


@allure.title("_output_weak / _json_parse_fail / _should_escalate branches")
def test_predicates() -> None:
    assert _output_weak("hi") is True
    assert _output_weak("error") is True
    # long-enough string that still matches the sentinel list (min_len bypassed)
    assert _output_weak("error", min_len=1) is True
    assert _output_weak("a real answer with length") is False
    # no triggers match → falls through to False
    assert _should_escalate("plain text", profile="p", triggers=()) is False

    assert _json_parse_fail("plain text") is False
    assert _json_parse_fail('{"ok": true}') is False
    assert _json_parse_fail("{bad json") is True

    assert _should_escalate("x", profile="p:escalate", triggers=("explicit_profile",)) is True
    assert _should_escalate("x", profile="p:escalate", triggers=("empty_output",)) is False
    assert _should_escalate("short", profile="p", triggers=("empty_output",)) is True
    assert _should_escalate("{bad", profile="p", triggers=("json_parse_fail",)) is True
    assert _should_escalate("I am unsure about this", profile="p", triggers=("low_confidence",)) is True
    assert _should_escalate("confident full answer", profile="p", triggers=("low_confidence",)) is False


@allure.title("invoke_result_to_dict serialises full result")
def test_result_to_dict() -> None:
    result = InvokeResult(
        text="t", model_id="m", profile="p", tier_billing="cheap",
        escalated_from="", eval_tokens=5, cost_usd=0.0, duration_ms=3, attempts=["m"],
    )
    d = invoke_result_to_dict(result)
    assert d["ok"] is True and d["escalated_from"] is None and d["attempts"] == ["m"]


def _write_cfg(root: Path, cfg: dict) -> None:
    (root / ".greedy-token.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


@pytest.fixture
def cheap_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("greedy_token.model_select.user_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(tmp_path))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    return tmp_path


@allure.title("invoke logs a route event when log=True")
def test_invoke_logs(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]},
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("full useful answer text", 11))
    result = invoke_profile("p", system="s", user="classify", root=cheap_root, log=True, allow_escalate=False)
    assert result.text == "full useful answer text"
    log = cheap_root / "usage.jsonl"
    assert log.is_file() and log.read_text(encoding="utf-8").strip()


@allure.title("chat failure on first candidate falls through to next, then raises")
def test_invoke_all_fail(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {"cheap": {"models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]},
                "escalation": {"enabled": False}}
    })

    def boom(*a, **k):
        raise RuntimeError("chat down")

    monkeypatch.setattr(llm_invoke, "llm_chat", boom)
    with pytest.raises(RuntimeError, match="LLM invoke failed"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=False, allow_escalate=False)


@allure.title("escalation: weak output on primary escalates to next model")
def test_invoke_escalates(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"], "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    seq = iter([("x", 1), ("a full strong answer here", 9)])
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: next(seq))
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=False, allow_escalate=True)
    assert result.escalated_from == "fast"
    assert result.model_id == "big"
    assert result.attempts == ["fast", "big"]


@allure.title("expensive candidate blocked by spend guard is skipped")
def test_invoke_expensive_blocked(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "policy": "expensive_only",
            "expensive": {
                "opt_in": True,
                "models": [{"id": "yandex-lite", "enabled": True, "provider": "yandex_gpt",
                            "model": "yandexgpt-lite", "profiles": ["p"], "cost_per_1m_usd": 100}],
            },
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(
        llm_invoke, "check_metered_allowed", lambda *a, **k: SpendDecision(allowed=False, reason="capped")
    )
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("should not be called", 1))
    with pytest.raises(RuntimeError, match="capped"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=False, allow_escalate=False, allow_expensive=False)


@allure.title("expensive candidate allowed by spend guard proceeds to chat")
def test_invoke_expensive_allowed(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "policy": "expensive_only",
            "expensive": {
                "opt_in": True,
                "models": [{"id": "yandex-lite", "enabled": True, "provider": "yandex_gpt",
                            "model": "yandexgpt-lite", "profiles": ["p"], "cost_per_1m_usd": 1}],
            },
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(
        llm_invoke, "check_metered_allowed", lambda *a, **k: SpendDecision(allowed=True)
    )
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("expensive strong answer", 20))
    result = invoke_profile(
        "p", system="s", user="u", root=cheap_root, log=False, allow_escalate=False, allow_expensive=True
    )
    assert result.text == "expensive strong answer"
    assert result.tier_billing == "expensive"
