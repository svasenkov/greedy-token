"""Public-contract tests for llm_invoke escalation / expensive / logging (fail_under=100)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import allure
from greedy_token import llm_invoke
from greedy_token.calibration import SOURCE_FIXED
from greedy_token.cheap_llm import MalformedResponseError
from greedy_token.llm_invoke import (
    InvokeResult,
    _invoke_decision,
    _invoke_tier,
    _json_parse_fail,
    _output_weak,
    _redact_error,
    _settle_call_cost,
    _should_escalate,
    invoke_profile,
    invoke_result_to_dict,
)
from greedy_token.model_select import ModelSpec, resolve_model
from greedy_token.spend_guard import SpendReservation

pytestmark = pytest.mark.unit


@allure.title("_output_weak / _json_parse_fail / _should_escalate branches")
def test_predicates() -> None:
    assert _output_weak("hi") is True
    assert _output_weak("error") is True
    # long-enough string that still matches the sentinel list (min_len bypassed)
    assert _output_weak("error", min_len=1) is True
    # exactly min_len chars and not a sentinel word — the boundary stays strict
    assert _output_weak("12345678") is False
    assert _output_weak("123456789") is False
    for word in ("null", "none", "n/a", "error"):
        assert _output_weak(word, min_len=1) is True
        assert _output_weak(word.upper(), min_len=1) is True
    assert _output_weak("a real answer with length") is False
    # no triggers match → falls through to False
    assert _should_escalate("plain text", profile="p", triggers=()) is False

    assert _json_parse_fail("plain text") is False
    assert _json_parse_fail('{"ok": true}') is False
    assert _json_parse_fail("{bad json") is True
    assert _json_parse_fail("[bad json") is True
    assert _json_parse_fail("[1, 2]") is False

    assert _should_escalate("x", profile="p:escalate", triggers=("explicit_profile",)) is True
    assert _should_escalate("x", profile="p:escalate", triggers=("empty_output",)) is False
    assert _should_escalate("short", profile="p", triggers=("empty_output",)) is True
    assert _should_escalate("{bad", profile="p", triggers=("json_parse_fail",)) is True
    # json_parse_fail must fire only when the trigger is configured
    assert _should_escalate("{bad", profile="p", triggers=()) is False
    assert _should_escalate("I am unsure about this", profile="p", triggers=("low_confidence",)) is True
    # the flagless-pattern stays case-insensitive
    assert _should_escalate("I AM UNSURE", profile="p", triggers=("low_confidence",)) is True
    assert _should_escalate("confident full answer", profile="p", triggers=("low_confidence",)) is False


@allure.title("_invoke_tier maps billing to executor tiers")
def test_invoke_tier() -> None:
    assert _invoke_tier(SimpleNamespace(billing_tier="cheap")) == "ollama"  # type: ignore[arg-type]
    assert _invoke_tier(SimpleNamespace(billing_tier="expensive")) == "cursor"  # type: ignore[arg-type]


@allure.title("_invoke_decision fills every RouteDecision field")
def test_invoke_decision_fields() -> None:
    decision = _invoke_decision("p", "ollama", 42)
    assert decision.target == "ollama"
    assert decision.route_id == "llm-p"
    assert decision.confidence == 1.0
    assert decision.confidence_source == SOURCE_FIXED
    assert decision.matched == ["p"]
    assert decision.command is None
    assert decision.note == ""
    assert decision.domains == []
    assert decision.est_tokens == 42


@allure.title("_redact_error strips secrets with the literal marker")
def test_redact_error_markers() -> None:
    assert _redact_error("key=sekrit", secrets=("sekrit",)) == "key=<redacted>"
    # secrets absent from the text must not corrupt it
    assert _redact_error("plain", secrets=("sekrit",)) == "plain"
    out = _redact_error("fail https://user:pw@host/path")
    assert out == "fail https://<redacted>@host/path"
    out = _redact_error("401 authorization: Bearer abc.def")
    assert out == "401 authorization: <redacted>"
    out = _redact_error("oops api_key=abcdef123 more")
    assert out == "oops api_key=<redacted> more"
    out = _redact_error("leak sk-abcdef123 tail")
    assert out == "leak <redacted> tail"


@allure.title("invoke_result_to_dict serialises full result")
def test_result_to_dict() -> None:
    result = InvokeResult(
        text="t", model_id="m", profile="p", tier_billing="cheap",
        escalated_from="", eval_tokens=5, cost_usd=0.0, duration_ms=3, attempts=["m"],
    )
    d = invoke_result_to_dict(result)
    assert d == {
        "ok": True,
        "text": "t",
        "model_id": "m",
        "profile": "p",
        "tier_billing": "cheap",
        "escalated_from": None,
        "eval_tokens": 5,
        "cost_usd": 0.0,
        "duration_ms": 3,
        "attempts": ["m"],
    }


@allure.title("_settle_call_cost: non-finite estimate degrades to 0; no eval owes the estimate")
def test_settle_call_cost_edges() -> None:
    inf_spec = ModelSpec(  # type: ignore[arg-type]
        id="inf",
        enabled=True,
        provider="openai_compat",
        url="https://x",
        model="m",
        profiles=("*",),
        locality="remote",
        billing="metered",
        cost_per_1m_usd=float("inf"),
    )
    # inf eval cost must not propagate into spend math — degrade to 0.0.
    assert _settle_call_cost(inf_spec, None, 100) == 0.0

    cheap_spec = ModelSpec(  # type: ignore[arg-type]
        id="m",
        enabled=True,
        provider="openai_compat",
        url="https://x",
        model="m",
        profiles=("*",),
        locality="remote",
        billing="metered",
        cost_per_1m_usd=0.2,
    )
    # Provider never reported usage: the call still owes what was reserved.
    reservation = SpendReservation(allowed=True, est_usd=0.5)
    assert _settle_call_cost(cheap_spec, reservation, None) == 0.5


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
        llm_invoke, "reserve_metered_call",
        lambda *a, **k: SpendReservation(allowed=False, reason="capped"),
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
        llm_invoke, "reserve_metered_call",
        lambda *a, **k: SpendReservation(allowed=True),
    )
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("expensive strong answer", 20))
    result = invoke_profile(
        "p", system="s", user="u", root=cheap_root, log=False, allow_escalate=False, allow_expensive=True
    )
    assert result.text == "expensive strong answer"
    assert result.tier_billing == "expensive"


@allure.title("metered cheap model denied without opt-in — provider is never called")
def test_invoke_metered_denied_no_http(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [{
                    "id": "bulk", "enabled": True, "model": "bulk-m",
                    "profiles": ["p"], "billing": "metered", "cost_per_1m_usd": 0.1,
                }]
            },
            "escalation": {"enabled": False},
        }
    })
    calls: list = []
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: calls.append(a) or ("x", 1))
    with pytest.raises(RuntimeError, match="metered LLM opt-in required"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=False, allow_escalate=False)
    assert calls == []


@allure.title("metered escalation counts spend for every completed attempt, not only the last")
def test_invoke_metered_attempts_accounted(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Costs stay at/below the cheap-tier threshold ($0.2/1M) so both models
    # derive "cheap" and only the metered opt-in applies.
    _write_cfg(cheap_root, {
        "llm": {
            "metered": {"opt_in": True},
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.1},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    seq = iter([("x", 100), ("a full strong answer here", 200)])
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: next(seq))
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=False, allow_escalate=True)
    assert result.model_id == "big"
    assert result.attempts == ["fast", "big"]
    # 100 tokens @ $0.1/1M + 200 tokens @ $0.2/1M — spend accrues per
    # completed call, not just for the model that served.
    assert result.cost_usd == pytest.approx(0.00001 + 0.00004)


@allure.title("logged invoke writes one event per completed call plus the operation outcome")
def test_invoke_event_records_attempts(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    _write_cfg(cheap_root, {
        "llm": {
            "metered": {"opt_in": True},
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.1},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    seq = iter([("x", 100), ("a full strong answer here", 200)])
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: next(seq))
    invoke_profile("p", system="s", user="u", root=cheap_root, log=True, allow_escalate=True)
    rows = [
        json.loads(line)
        for line in (cheap_root / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = [row for row in rows if row.get("cmd") == "llm"]
    # One request event per completed provider call, each with its own spend.
    assert [row["billing"]["model_id"] for row in req] == ["fast", "big"]
    assert req[0]["cost_usd"] == pytest.approx(0.00001)
    assert req[1]["cost_usd"] == pytest.approx(0.00004)
    assert all(row["llm_attempts"] == ["fast", "big"] for row in req)
    assert all(row["phase"] == "executed" for row in req)
    assert all(row["billing"]["tier"] == "metered" for row in req)
    assert len({row["operation_id"] for row in req}) == 1
    # Only the escalated call carries the escalation marker.
    assert "escalated_from" not in req[0]
    assert req[1]["escalated_from"] == "fast"
    # The weak first answer earned no savings. The serving call's earned
    # figure stays unknown too — no boundary declared an authoritative
    # turn-skip — and the formula delta is carried only as potential.
    assert req[0]["savings_exclusion"] == "empty_result"
    assert req[0]["cursor_saved"] == 0
    assert req[1]["savings_scope"] == "unknown"
    assert req[1]["cursor_saved"] is None
    assert req[1]["cursor_saved_potential"] > 0
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "success"
    assert outcome["operation_id"] == req[0]["operation_id"]
    assert outcome["attempts"] == 2
    assert outcome["retries"] == 1


@allure.title("failed escalation still logs the completed call's spend")
def test_invoke_failed_chain_logs_cost(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    _write_cfg(cheap_root, {
        "llm": {
            "metered": {"opt_in": True},
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.1},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })

    seq = iter([("x", 100)])

    def flaky(*a, **k):
        try:
            return next(seq)
        except StopIteration:
            raise RuntimeError("chat down") from None

    monkeypatch.setattr(llm_invoke, "llm_chat", flaky)
    with pytest.raises(RuntimeError, match="chat down"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=True, allow_escalate=True)
    rows = [
        json.loads(line)
        for line in (cheap_root / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = [row for row in rows if row.get("cmd") == "llm"]
    # The completed metered call is logged even though the chain failed.
    assert len(req) == 1
    assert req[0]["billing"]["model_id"] == "fast"
    assert req[0]["cost_usd"] == pytest.approx(0.00001)
    assert req[0]["phase"] == "executed"
    assert req[0]["billing"]["tier"] == "metered"
    assert req[0]["operation_id"]
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"
    assert outcome["operation_id"] == req[0]["operation_id"]


@allure.title("invoke with no completed call logs a planned refusal, not silence")
def test_invoke_no_completed_call_logged(
    cheap_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{
                "id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
            }]},
            "escalation": {"enabled": False},
        }
    })

    def boom(*a, **k):
        raise RuntimeError("chat down")

    monkeypatch.setattr(llm_invoke, "llm_chat", boom)
    with pytest.raises(RuntimeError, match="LLM invoke failed"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=True, allow_escalate=False)
    rows = [
        json.loads(line)
        for line in (cheap_root / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = [row for row in rows if row.get("cmd") == "llm"]
    assert len(req) == 1
    assert req[0]["phase"] == "planned"
    assert req[0]["executor"]["executed"] is False
    assert req[0]["savings_exclusion"] == "not_executed"
    assert req[0]["cost_usd"] == 0.0
    assert req[0]["llm_attempts"] == ["fast"]
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"
    assert outcome["operation_id"] == req[0]["operation_id"]


@allure.title("intra-chain cap: settled spend of call one blocks candidate two")
def test_invoke_chain_respects_spend_cap(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the spend guard used to read persisted telemetry only after
    the whole chain, so both calls passed a cap their sum exceeded."""
    _write_cfg(cheap_root, {
        "llm": {
            "metered": {"opt_in": True},
            "expensive": {"daily_cap_usd": 0.1},
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    calls: list[str] = []
    seq = iter([("", 500_000), ("a full strong answer here", 10)])

    def chat(model, **k):
        calls.append(model.model_id)
        return next(seq)

    monkeypatch.setattr(llm_invoke, "llm_chat", chat)
    with pytest.raises(RuntimeError, match="cap"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=False, allow_escalate=True)
    # fast paid 500_000 × $0.2/1M = $0.10 and settled it immediately — the
    # cap check for big saw it and the second provider call never happened.
    assert calls == ["fast"]
    from greedy_token.spend_ledger import ledger_spend_usd

    assert ledger_spend_usd() == pytest.approx(0.1)


@allure.title("weak output on the last candidate is a failure, not a saved success")
def test_invoke_weak_final_output_fails(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: an empty final response used to be marked success and
    credited savings. Now it is a failed operation with no savings."""
    import json

    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    seq = iter([("x", 1), ("", 2)])
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: next(seq))
    with pytest.raises(RuntimeError, match="rejected"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=True, allow_escalate=True)
    rows = [
        json.loads(line)
        for line in (cheap_root / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = [row for row in rows if row.get("cmd") == "llm"]
    assert len(req) == 2
    assert req[1]["gate_reason"] == "output_empty"
    assert req[1]["cursor_saved"] == 0
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"


@allure.title("malformed provider response records a structured failed call with spend")
def test_invoke_malformed_response(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: choices:[] used to escape as IndexError and lose all
    telemetry for a paid call. Now it is a structured failure whose cost
    still reaches the spend ledger."""
    import json

    from greedy_token.cheap_llm import MalformedResponseError

    _write_cfg(cheap_root, {
        "llm": {
            "metered": {"opt_in": True},
            "cheap": {
                "models": [
                    {"id": "bulk", "enabled": True, "model": "m", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                ]
            },
            "escalation": {"enabled": False},
        }
    })

    def bad(*a, **k):
        raise MalformedResponseError("openai_compat response has no choices", eval_tokens=500)

    monkeypatch.setattr(llm_invoke, "llm_chat", bad)
    with pytest.raises(RuntimeError, match="no choices"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=True, allow_escalate=False)
    rows = [
        json.loads(line)
        for line in (cheap_root / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = [row for row in rows if row.get("cmd") == "llm"]
    assert len(req) == 1
    assert req[0]["phase"] == "executed"
    assert req[0]["cost_usd"] == pytest.approx(500 * 0.2 / 1_000_000)
    assert req[0]["cursor_saved"] == 0
    outcome = next(row for row in rows if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"

    from greedy_token.spend_ledger import ledger_spend_usd

    assert ledger_spend_usd() == pytest.approx(0.0001)


@allure.title("served-but-weak output claims no savings even without escalation")
def test_invoke_weak_served_output_no_savings(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]},
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("", 5))
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=True, allow_escalate=False)
    # The text is still returned to the caller — but the gate no longer
    # lets an empty answer claim savings.
    assert result.text == ""
    rows = [
        json.loads(line)
        for line in (cheap_root / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    req = [row for row in rows if row.get("cmd") == "llm"]
    assert req[0]["savings_exclusion"] == "empty_result"
    assert req[0]["cursor_saved"] == 0


# ---------------------------------------------------------------------------
# Mutation-hardening: dense asserts on forwarded kwargs, result fields and
# every key the logged events must carry.
# ---------------------------------------------------------------------------


def _read_events(root: Path) -> list[dict]:
    import json

    return [
        json.loads(line)
        for line in (root / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@allure.title("invoke forwards system/user/timeout verbatim to llm_chat")
def test_invoke_forwards_call_kwargs(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]},
            "escalation": {"enabled": False},
        }
    })
    captured: dict = {}

    def stub(candidate, **kwargs):
        captured.update(kwargs)
        return ("a complete useful answer", 10)

    monkeypatch.setattr(llm_invoke, "llm_chat", stub)
    result = invoke_profile("p", system="sys", user="usr", root=cheap_root, log=False)
    assert captured == {"system": "sys", "user": "usr", "timeout": 120.0}
    assert result.text == "a complete useful answer"
    assert result.model_id == "fast"
    assert result.profile == "p"
    assert result.tier_billing == "cheap"
    assert result.escalated_from == ""
    assert result.eval_tokens == 10
    assert result.attempts == ["fast"]
    assert isinstance(result.duration_ms, int)


@allure.title("profile resolution honours the model's profile match")
def test_invoke_resolves_by_profile(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "other", "enabled": True, "model": "o", "profiles": ["q"]},
                    {"id": "match", "enabled": True, "model": "m", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("a complete useful answer", 5))
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=False)
    assert result.model_id == "match"


@allure.title("explicit resolved model bypasses profile resolution")
def test_invoke_resolved_override(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "other", "enabled": True, "model": "o", "profiles": ["q"]},
                    {"id": "match", "enabled": True, "model": "m", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("a complete useful answer", 5))
    resolved = resolve_model("q", root=cheap_root)
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=False, resolved=resolved)
    assert result.model_id == "other"


@allure.title("escalation triggers come from the invoked root, not the env root")
def test_invoke_registry_uses_invoked_root(
    cheap_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # env root says empty_output is an escalation trigger; the invoked root
    # configures no triggers at all — the weak first answer must be served.
    _write_cfg(cheap_root, {
        "llm": {"escalation": {"enabled": True, "triggers": ["empty_output"]}}
    })
    other = tmp_path / "other-root"
    other.mkdir()
    _write_cfg(other, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "a", "enabled": True, "model": "m1", "profiles": ["p"]},
                    {"id": "b", "enabled": True, "model": "m2", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": True, "chain": ["a", "b"],
                           "triggers": ["json_parse_fail"], "max_steps": 2},
        }
    })
    seq = iter([("x", 1), ("a full strong answer here", 9)])
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: next(seq))
    result = invoke_profile("p", system="s", user="u", root=other, log=False)
    assert result.model_id == "a"
    assert result.text == "x"
    assert result.attempts == ["a"]


@allure.title("metered reservation receives cli_allow, root and operation_id")
def test_invoke_metered_reservation_kwargs(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    captured: dict = {}

    def fake_reserve(spec, **kwargs):
        captured.update(kwargs)
        return SpendReservation(allowed=True)

    monkeypatch.setattr(llm_invoke, "reserve_metered_call", fake_reserve)
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("expensive strong answer", 20))
    invoke_profile("p", system="s", user="u", root=cheap_root, log=False, allow_expensive=True)
    assert captured["cli_allow"] is True
    assert captured["root"] == cheap_root
    assert captured["operation_id"]
    assert captured["est_cost_usd"] > 0


@allure.title("denied reservation walks the whole candidate chain")
def test_invoke_denied_metered_chain_attempts(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.1},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    monkeypatch.setattr(
        llm_invoke, "reserve_metered_call",
        lambda *a, **k: SpendReservation(allowed=False, reason="capped"),
    )
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("should not be called", 1))
    with pytest.raises(RuntimeError, match="capped"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=True, allow_escalate=True)
    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert len(req) == 1
    # every denied candidate must still show up in the attempts list
    assert req[0]["llm_attempts"] == ["fast", "big"]


@allure.title("transport error on first candidate still serves the chain")
def test_invoke_first_error_second_serves(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })

    def flaky(candidate, **kwargs):
        if candidate.model_id == "fast":
            raise RuntimeError("chat down")
        return ("a full strong answer here", 9)

    monkeypatch.setattr(llm_invoke, "llm_chat", flaky)
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=False)
    assert result.model_id == "big"
    assert result.text == "a full strong answer here"
    assert result.attempts == ["fast", "big"]


@allure.title("malformed response then success: spend accrues per call, spend_ref is the ledger id")
def test_invoke_malformed_then_success(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.tokens import count_tokens

    _write_cfg(cheap_root, {
        "llm": {
            "metered": {"opt_in": True},
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.1},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })

    def chat(candidate, **kwargs):
        if candidate.model_id == "fast":
            raise MalformedResponseError("bad shape", eval_tokens=100)
        return ("a full strong answer here", 200)

    monkeypatch.setattr(llm_invoke, "llm_chat", chat)
    ticks = iter([100.0, 101.0, 102.0, 102.5, 103.5, 104.0])
    monkeypatch.setattr(
        llm_invoke, "time",
        SimpleNamespace(perf_counter=lambda: next(ticks, 105.0)),
    )
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=True)
    prompt = count_tokens("su").tokens
    # call 1: 100 tok @ $0.1/1M malformed attempt + call 2: 200 tok @ $0.2/1M
    assert result.cost_usd == pytest.approx(0.00001 + 0.00004)
    assert result.model_id == "big"
    assert result.attempts == ["fast", "big"]
    assert result.duration_ms == 4000

    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert [row["billing"]["model_id"] for row in req] == ["fast", "big"]
    # the malformed attempt: own est, own spend_ref, gate rejection
    assert req[0]["est_tokens"] == 100 + prompt
    assert req[0]["input_tokens"] == prompt
    assert req[0]["duration_ms"] == 1000
    assert len(req[0]["spend_ref"]) == 32
    assert req[0]["savings_exclusion"] == "empty_result"
    assert req[0]["cost_usd"] == pytest.approx(0.00001)
    # the serving call
    assert req[1]["est_tokens"] == 200 + prompt
    assert req[1]["duration_ms"] == 1000
    assert len(req[1]["spend_ref"]) == 32
    assert req[1]["cost_usd"] == pytest.approx(0.00004)
    outcome = next(row for row in _read_events(cheap_root) if row.get("event") == "route_outcome")
    assert outcome["duration_ms"] == 4000
    assert outcome["outcome"] == "success"


@allure.title("malformed mid-chain: earlier spend survives the failed call")
def test_invoke_malformed_mid_chain_keeps_prior_cost(
    cheap_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "metered": {"opt_in": True},
            "cheap": {
                "models": [
                    {"id": "a", "enabled": True, "model": "m1", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.1},
                    {"id": "b", "enabled": True, "model": "m2", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.2},
                    {"id": "c", "enabled": True, "model": "m3", "profiles": ["p"],
                     "billing": "metered", "cost_per_1m_usd": 0.15},
                ]
            },
            "escalation": {"enabled": True, "chain": ["a", "b", "c"],
                           "triggers": ["empty_output"], "max_steps": 3},
        }
    })

    def chat(candidate, **kwargs):
        if candidate.model_id == "a":
            return ("x", 100)
        if candidate.model_id == "b":
            raise MalformedResponseError("bad shape", eval_tokens=200)
        return ("a full strong answer here", 300)

    monkeypatch.setattr(llm_invoke, "llm_chat", chat)
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=False)
    assert result.model_id == "c"
    assert result.attempts == ["a", "b", "c"]
    # weak-paid + malformed-paid + serving call — the malformed `cost =` must
    # keep the earlier call's spend, not overwrite it.
    assert result.cost_usd == pytest.approx(0.00001 + 0.00004 + 0.000045)


@allure.title("provider error text is redacted before it reaches RuntimeError")
def test_invoke_error_redacts_secrets(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{
                "id": "fast", "enabled": True, "model": "m7", "profiles": ["p"],
                "api_key": "sk-secretkey99", "url": "https://prov.internal/v1",
            }]},
            "escalation": {"enabled": False},
        }
    })

    def boom(*a, **k):
        raise RuntimeError("401 with key sk-secretkey99 at https://prov.internal/v1")

    monkeypatch.setattr(llm_invoke, "llm_chat", boom)
    with pytest.raises(RuntimeError, match="LLM invoke failed") as excinfo:
        invoke_profile("p", system="s", user="u", root=cheap_root, log=False)
    msg = str(excinfo.value)
    assert "sk-secretkey99" not in msg
    assert "prov.internal" not in msg
    assert "<redacted>" in msg

    # the malformed-response path redacts provider secrets the same way —
    # the endpoint URL only leaves via the explicit secrets tuple (no regex
    # matches a bare host)
    def bad_shape(*a, **k):
        raise MalformedResponseError(
            "echoed https://prov.internal/v1 key sk-secretkey99", eval_tokens=3
        )

    monkeypatch.setattr(llm_invoke, "llm_chat", bad_shape)
    with pytest.raises(RuntimeError, match="LLM invoke failed") as excinfo:
        invoke_profile("p", system="s", user="u", root=cheap_root, log=False)
    msg = str(excinfo.value)
    assert "sk-secretkey99" not in msg
    assert "prov.internal" not in msg
    assert "<redacted>" in msg


@allure.title("explicit :escalate profile serves the final candidate's output")
def test_invoke_explicit_escalate_profile_serves_final(
    cheap_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "a", "enabled": True, "model": "m1", "profiles": ["p"]},
                    {"id": "b", "enabled": True, "model": "m2", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": True, "chain": ["a", "b"],
                           "triggers": ["explicit_profile"], "max_steps": 2},
        }
    })
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("decent answer here", 5))
    result = invoke_profile("p:escalate", system="s", user="u", root=cheap_root, log=False)
    assert result.model_id == "b"
    assert result.text == "decent answer here"


@allure.title("final model rejected by triggers raises with the trigger reason")
def test_invoke_final_model_rejected_message(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.tokens import count_tokens

    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "a", "enabled": True, "model": "m1", "profiles": ["p"]},
                    {"id": "b", "enabled": True, "model": "m2", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": True, "chain": ["a", "b"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    # weak output on every candidate, no provider usage reported
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("", None))
    ticks = iter([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    monkeypatch.setattr(
        llm_invoke, "time",
        SimpleNamespace(perf_counter=lambda: next(ticks, 106.0)),
    )
    with pytest.raises(RuntimeError, match="output rejected by escalation triggers"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=True)
    prompt = count_tokens("su").tokens
    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert len(req) == 2
    # no reported usage: estimate is prompt tokens alone
    assert all(row["est_tokens"] == prompt for row in req)
    assert all(row["input_tokens"] == prompt for row in req)
    outcome = next(row for row in _read_events(cheap_root) if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"
    assert outcome["escalations"] == ["b"]
    assert outcome["duration_ms"] == 5000


@allure.title("malformed response on a free model records no spend_ref")
def test_invoke_free_model_malformed(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]},
            "escalation": {"enabled": False},
        }
    })

    def bad(*a, **k):
        raise MalformedResponseError("no choices", eval_tokens=7)

    monkeypatch.setattr(llm_invoke, "llm_chat", bad)
    with pytest.raises(RuntimeError, match="no choices"):
        invoke_profile("p", system="s", user="u", root=cheap_root, log=True)
    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert len(req) == 1
    assert "spend_ref" not in req[0]
    assert req[0]["cost_usd"] == 0.0


@allure.title("logged invoke emits full-field request and outcome events")
def test_invoke_event_schema(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.result_contract import RESULT_NOT_EVALUATED
    from greedy_token.tokens import count_tokens

    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]},
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("a complete useful answer", 10))
    ticks = iter([100.0, 101.0, 102.0, 103.0])
    monkeypatch.setattr(
        llm_invoke, "time",
        SimpleNamespace(perf_counter=lambda: next(ticks, 104.0)),
    )
    result = invoke_profile(
        "p", system="s", user="u", root=cheap_root, log=True,
        allow_escalate=False, tags={"k": "v"}, parent_operation_id="par-1",
    )
    prompt = count_tokens("su").tokens

    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert len(req) == 1
    e = req[0]
    assert e["cmd"] == "llm"
    assert e["task"] == "llm invoke p"
    assert e["root"] == str(cheap_root)
    assert e["selected_tier"] == "ollama"
    assert e["route_id"] == "llm-p"
    assert e["confidence_source"] == SOURCE_FIXED
    assert e["matched"] == ["p"]
    assert e["profile"] == "p"
    assert e["billing_tier"] == "cheap"
    assert e["executor"]["model_id"] == "fast"
    assert e["executor"]["executed"] is True
    assert e["phase"] == "executed"
    assert e["est_tokens"] == 10 + prompt
    assert e["input_tokens"] == prompt
    assert e["duration_ms"] == 1000
    assert e["operation_id"]
    assert e["parent_operation_id"] == "par-1"
    assert e["llm_attempts"] == ["fast"]
    assert e["tags"] == {"k": "v"}
    assert e["cost_usd"] == 0.0
    assert e["result_status"] == RESULT_NOT_EVALUATED
    assert e["gate_action"] and e["gate_reason"]
    assert e["billing"]["model_id"] == "fast"
    assert e["billing"]["tier"] == "cheap"
    assert "spend_ref" not in e
    assert "escalated_from" not in e

    assert result.duration_ms == 3000
    assert result.escalated_from == ""

    outcome = next(row for row in _read_events(cheap_root) if row.get("event") == "route_outcome")
    assert outcome["root"] == str(cheap_root)
    assert outcome["selected_tier"] == "ollama"
    assert outcome["route_id"] == "llm-p"
    assert outcome["confidence_source"] == SOURCE_FIXED
    assert outcome["outcome"] == "success"
    assert outcome["outcome_layer"] == "executor"
    assert outcome["attempts"] == 1
    assert outcome["retries"] == 0
    assert outcome["escalations"] == []
    assert outcome["est_tokens"] == 0
    assert outcome["duration_ms"] == 3000
    assert outcome["operation_id"] == e["operation_id"]
    assert outcome["parent_operation_id"] == "par-1"
    assert outcome["result_status"] == RESULT_NOT_EVALUATED
    assert outcome["savings_eligible"] is True


@allure.title("cross-tier escalation: outcome tier comes from the last call")
def test_invoke_cross_tier_escalation_events(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "expensive": {
                "opt_in": True,
                "models": [{"id": "big", "enabled": True, "provider": "yandex_gpt",
                            "model": "yandexgpt-pro", "profiles": ["p"], "cost_per_1m_usd": 1}],
            },
            "cheap": {
                "models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}],
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    monkeypatch.setattr(
        llm_invoke, "reserve_metered_call",
        lambda *a, **k: SpendReservation(allowed=True, reservation_id="res-1"),
    )
    seq = iter([("x", 1), ("a full strong answer here", 9)])
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: next(seq))
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=True,
                            allow_expensive=True)
    assert result.model_id == "big"
    assert result.tier_billing == "expensive"
    assert result.escalated_from == "fast"
    assert result.attempts == ["fast", "big"]

    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert [row["selected_tier"] for row in req] == ["ollama", "cursor"]
    assert [row["billing_tier"] for row in req] == ["cheap", "expensive"]
    assert req[1]["escalated_from"] == "fast"
    assert "escalated_from" not in req[0]
    outcome = next(row for row in _read_events(cheap_root) if row.get("event") == "route_outcome")
    # outcome tier and escalation list reflect the serving call, not the first
    assert outcome["selected_tier"] == "cursor"
    assert outcome["escalations"] == ["big"]
    assert outcome["attempts"] == 2
    assert outcome["retries"] == 1
    assert outcome["result_status"] == "not_evaluated"


@allure.title("invoke without allow_escalate still escalates when the chain is configured")
def test_invoke_escalates_by_default(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {
                "models": [
                    {"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]},
                    {"id": "big", "enabled": True, "model": "m70", "profiles": ["p"]},
                ]
            },
            "escalation": {"enabled": True, "chain": ["fast", "big"],
                           "triggers": ["empty_output"], "max_steps": 2},
        }
    })
    seq = iter([("x", 1), ("a full strong answer here", 9)])
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: next(seq))
    result = invoke_profile("p", system="s", user="u", root=cheap_root, log=False)
    assert result.model_id == "big"
    assert result.attempts == ["fast", "big"]


@allure.title("denied invoke logs a full refusal event")
def test_invoke_refusal_event_schema(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token.result_contract import RESULT_NOT_EVALUATED
    from greedy_token.tokens import count_tokens

    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{
                "id": "bulk", "enabled": True, "model": "bulk-m",
                "profiles": ["p"], "billing": "metered", "cost_per_1m_usd": 0.1,
            }]},
            "escalation": {"enabled": False},
        }
    })
    with pytest.raises(RuntimeError, match="metered LLM opt-in required"):
        invoke_profile(
            "p", system="s", user="u", root=cheap_root, log=True,
            tags={"k": "v"}, parent_operation_id="par-1",
        )
    prompt = count_tokens("su").tokens
    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert len(req) == 1
    e = req[0]
    assert e["cmd"] == "llm"
    assert e["root"] == str(cheap_root)
    assert e["selected_tier"] == "ollama"
    assert e["route_id"] == "llm-p"
    assert e["matched"] == ["p"]
    assert e["profile"] == "p"
    assert e["billing_tier"] == "cheap"
    assert e["phase"] == "planned"
    assert e["executor"]["executed"] is False
    assert e["est_tokens"] == prompt
    assert e["input_tokens"] == prompt
    assert e["cost_usd"] == 0.0
    assert e["llm_attempts"] == ["bulk"]
    assert e["savings_exclusion"] == "not_executed"
    assert e["result_status"] == RESULT_NOT_EVALUATED
    assert e["gate_action"] == "bypassed"
    assert e["gate_reason"] == "not_started"
    assert e["tags"] == {"k": "v"}
    assert e["operation_id"]
    assert e["parent_operation_id"] == "par-1"
    assert e["billing"]["tier"] == "metered"
    outcome = next(row for row in _read_events(cheap_root) if row.get("event") == "route_outcome")
    assert outcome["outcome"] == "failure"
    assert outcome["attempts"] == 1
    assert outcome["retries"] == 0
    assert outcome["escalations"] == []
    assert outcome["duration_ms"] >= 0
    assert outcome["operation_id"] == e["operation_id"]
    assert outcome["parent_operation_id"] == "par-1"


@allure.title("invoke with root=None resolves through env and logs '.' root")
def test_invoke_env_root_event(cheap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cfg(cheap_root, {
        "llm": {
            "cheap": {"models": [{"id": "fast", "enabled": True, "model": "m7", "profiles": ["p"]}]},
            "escalation": {"enabled": False},
        }
    })
    monkeypatch.setattr(llm_invoke, "llm_chat", lambda *a, **k: ("a complete useful answer", 5))
    invoke_profile("p", system="s", user="u", log=True, allow_escalate=False)
    req = [row for row in _read_events(cheap_root) if row.get("cmd") == "llm"]
    assert req[0]["root"] == "."
