"""Public-contract tests for advisory log / watch (fail_under=100)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import allure
import pytest

from greedy_token import advisory

pytestmark = [
    allure.epic("Advisory"),
    allure.parent_suite("Advisory"),
    allure.feature("Hook advisory log"),
    allure.suite("Advisory"),
]


@pytest.fixture
def advisory_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "advisory.jsonl"
    monkeypatch.setenv("GREEDY_ADVISORY_LOG", str(log))
    monkeypatch.delenv("GREEDY_ADVISORY", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_TTY", raising=False)
    return log


@allure.title("advisory_log_path honours env and default")
def test_advisory_log_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GREEDY_ADVISORY_LOG", str(tmp_path / "a.jsonl"))
    assert advisory.advisory_log_path() == tmp_path / "a.jsonl"
    monkeypatch.delenv("GREEDY_ADVISORY_LOG", raising=False)
    assert advisory.advisory_log_path() == advisory.DEFAULT_ADVISORY_LOG


@allure.title("env toggles: enabled / overkill gate / threshold / tty")
def test_env_toggles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GREEDY_ADVISORY", raising=False)
    assert advisory.advisory_enabled() is True
    monkeypatch.setenv("GREEDY_ADVISORY", "0")
    assert advisory.advisory_enabled() is False

    monkeypatch.delenv("GREEDY_OVERKILL_GATE", raising=False)
    assert advisory.overkill_gate_enabled() is False
    monkeypatch.setenv("GREEDY_OVERKILL_GATE", "on")
    assert advisory.overkill_gate_enabled() is True

    monkeypatch.setenv("GREEDY_OVERKILL_ATTACHMENTS", "5")
    assert advisory.overkill_attachment_threshold() == 5
    monkeypatch.setenv("GREEDY_OVERKILL_ATTACHMENTS", "not-a-number")
    assert advisory.overkill_attachment_threshold() == 3
    monkeypatch.delenv("GREEDY_OVERKILL_ATTACHMENTS", raising=False)
    assert advisory.overkill_attachment_threshold() == 3

    monkeypatch.delenv("GREEDY_TOKEN_TTY", raising=False)
    assert advisory.tty_path() is None
    monkeypatch.setenv("GREEDY_TOKEN_TTY", str(tmp_path / "tty"))
    assert advisory.tty_path() == tmp_path / "tty"


@allure.title("_utc_now_iso / _truncate / parse_attachments")
def test_small_helpers() -> None:
    assert advisory._utc_now_iso().endswith("Z")
    assert advisory._truncate("short") == "short"
    long = advisory._truncate("x" * 500, limit=10)
    assert long.endswith("…")
    assert len(long) == 10

    data = {
        "attachments": [
            {"file_path": "a.py"},
            {"path": "b.py"},
            {"nope": "c"},
            "not-a-dict",
        ]
    }
    assert advisory.parse_attachments(data) == ["a.py", "b.py"]
    assert advisory.parse_attachments({}) == []


@allure.title("is_question_like and is_overkill branches")
def test_question_and_overkill(monkeypatch: pytest.MonkeyPatch) -> None:
    assert advisory.is_question_like("what is this") is True
    assert advisory.is_question_like("fix what is broken") is False

    # non-cursor target
    assert advisory.is_overkill("what?", route_id="r", target="ollama", attachment_count=9) is False
    # edit verb, not fallback → False
    assert (
        advisory.is_overkill("implement this", route_id="tool-rg", target="cursor", attachment_count=9)
        is False
    )
    # not question-like → False
    assert advisory.is_overkill("random text", route_id="cursor-fallback", target="cursor", attachment_count=9) is False

    # threshold 0 → only fallback triggers
    monkeypatch.setenv("GREEDY_OVERKILL_ATTACHMENTS", "0")
    assert advisory.is_overkill("what is x", route_id="cursor-fallback", target="cursor", attachment_count=0) is True
    assert advisory.is_overkill("what is x", route_id="cursor-plan", target="cursor", attachment_count=0) is False

    # threshold >0 branches
    monkeypatch.setenv("GREEDY_OVERKILL_ATTACHMENTS", "3")
    assert advisory.is_overkill("how do i x", route_id="cursor-plan", target="cursor", attachment_count=3) is True
    assert advisory.is_overkill("how do i x", route_id="cursor-fallback", target="cursor", attachment_count=1) is True
    assert advisory.is_overkill("how do i x", route_id="cursor-plan", target="cursor", attachment_count=1) is False


@allure.title("overkill_recommendations with and without attachments")
def test_overkill_recommendations() -> None:
    recs = advisory.overkill_recommendations(prompt="p", attachment_count=2, est_tokens=1234, route_id="cursor-fallback")
    assert any("Attachments: 2" in r for r in recs)
    recs0 = advisory.overkill_recommendations(prompt="p", attachment_count=0, est_tokens=1, route_id="r")
    assert not any("Attachments" in r for r in recs0)


def _decision(**kw):
    base = dict(target="cursor", route_id="cursor-fallback", confidence=0.4, est_tokens=9000)
    base.update(kw)
    return SimpleNamespace(**base)


@allure.title("build_event / to_dict / event_from_dict roundtrip")
def test_build_and_roundtrip() -> None:
    event = advisory.build_event(
        kind=advisory.KIND_OVERKILL,
        action="warn",
        prompt="  do something  ",
        decision=_decision(),
        data={"attachments": [{"file_path": "a"}], "session_id": "s1", "composer_mode": "agent"},
        blocked=True,
        recommendations=["r1"],
    )
    assert event.prompt == "do something"
    assert event.attachment_count == 1
    assert event.session_id == "s1"
    d = event.to_dict()
    assert d["kind"] == advisory.KIND_OVERKILL
    restored = advisory.event_from_dict(d)
    assert restored.kind == event.kind
    assert restored.blocked is True

    # decision missing attrs → defaults; conversation_id fallback
    ev2 = advisory.build_event(
        kind=advisory.KIND_PASS,
        action="pass",
        prompt="p",
        decision=object(),
        data={"conversation_id": "c2"},
    )
    assert ev2.target == "cursor"
    assert ev2.session_id == "c2"


@allure.title("append_event respects enabled flag; emit writes tty")
def test_append_and_emit(advisory_log: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = advisory.build_event(
        kind=advisory.KIND_INTERCEPT,
        action="route",
        prompt="p",
        decision=_decision(),
        data={},
    )
    advisory.append_event(event)
    assert advisory_log.is_file()
    assert advisory_log.read_text(encoding="utf-8").strip()

    monkeypatch.setenv("GREEDY_ADVISORY", "0")
    other = tmp_path / "disabled.jsonl"
    monkeypatch.setenv("GREEDY_ADVISORY_LOG", str(other))
    advisory.append_event(event)
    assert not other.exists()

    monkeypatch.setenv("GREEDY_ADVISORY", "1")
    tty = tmp_path / "tty.out"
    monkeypatch.setenv("GREEDY_TOKEN_TTY", str(tty))
    advisory.emit_advisory(event)
    assert tty.read_text(encoding="utf-8")


@allure.title("write_tty: none, ok, and OSError swallowed")
def test_write_tty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = advisory.build_event(
        kind=advisory.KIND_BYPASS, action="bypass", prompt="p", decision=_decision(), data={}
    )
    monkeypatch.delenv("GREEDY_TOKEN_TTY", raising=False)
    advisory.write_tty(event)  # no tty → no-op

    # OSError path: tty points at a directory → open("w") raises, swallowed
    a_dir = tmp_path / "dir-tty"
    a_dir.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_TTY", str(a_dir))
    advisory.write_tty(event)


@allure.title("format_terminal_block covers kinds, blocked, attachments, recs")
def test_format_terminal_block() -> None:
    ev = advisory.AdvisoryEvent(
        ts="t",
        kind=advisory.KIND_OVERKILL,
        action="warn",
        prompt="p",
        target="cursor",
        route_id="cursor-fallback",
        confidence=0.4,
        est_tokens=9000,
        attachment_count=2,
        recommendations=["do x"],
        blocked=True,
    )
    block = advisory.format_terminal_block(ev)
    assert "OVERKILL" in block
    assert "BLOCKED" in block
    assert "attachments: 2" in block
    assert "do x" in block

    ev2 = advisory.AdvisoryEvent(
        ts="t", kind="custom-kind", action="pass", prompt="p", target="cursor",
        route_id="r", confidence=1.0, est_tokens=1,
    )
    assert "CUSTOM-KIND" in advisory.format_terminal_block(ev2)


@allure.title("format_overkill_user_message includes preview and recs")
def test_format_overkill_user_message() -> None:
    msg = advisory.format_overkill_user_message(
        "fix the thing", attachment_count=1, est_tokens=9000, route_id="cursor-fallback"
    )
    assert "Agent overkill" in msg
    assert "fix the thing" in msg
    assert "cursor:" in msg


@allure.title("watch_events: creates missing log then exits when not following")
def test_watch_creates_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "missing.jsonl"
    monkeypatch.setenv("GREEDY_ADVISORY_LOG", str(log))
    assert advisory.watch_events(follow=False) == 0
    assert log.is_file()


@allure.title("watch_events: from_start reads events (text + json), skips junk")
def test_watch_from_start(advisory_log: Path, capsys) -> None:
    ev = advisory.build_event(
        kind=advisory.KIND_INTERCEPT, action="route", prompt="hello", decision=_decision(), data={}
    )
    advisory_log.write_text(
        json.dumps(ev.to_dict()) + "\n\nnot-json-line\n", encoding="utf-8"
    )
    assert advisory.watch_events(follow=False, from_start=True, json_out=False) == 0
    out = capsys.readouterr().out
    assert "INTERCEPT" in out

    assert advisory.watch_events(follow=False, from_start=True, json_out=True) == 0
    out2 = capsys.readouterr().out
    assert '"kind"' in out2


@allure.title("watch_events: follow loop drains, handles truncation + missing file, stops on Ctrl-C")
def test_watch_follow_loop(advisory_log: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    ev = advisory.build_event(
        kind=advisory.KIND_PASS, action="pass", prompt="tick", decision=_decision(), data={}
    )
    # Large initial content so seen_pos starts high (follow, from_start=False).
    advisory_log.write_text((json.dumps(ev.to_dict()) + "\n") * 5, encoding="utf-8")

    calls = {"n": 0}
    short = json.dumps(ev.to_dict()) + "\n"

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            # truncate below seen_pos → drain resets seen_pos and re-reads
            advisory_log.write_text(short, encoding="utf-8")
        elif calls["n"] == 2:
            # file disappears → drain returns early (not is_file)
            advisory_log.unlink()
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(advisory.time, "sleep", fake_sleep)
    assert advisory.watch_events(follow=True, from_start=False, json_out=True) == 0
    assert calls["n"] >= 3
