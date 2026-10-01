"""Public-contract tests for advisory log / watch (fail_under=100)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import allure
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

    # Success path: the terminal block is written verbatim.
    tty = tmp_path / "tty.txt"
    monkeypatch.setenv("GREEDY_TOKEN_TTY", str(tty))
    advisory.write_tty(event)
    written = tty.read_text(encoding="utf-8")
    assert written == advisory.format_terminal_block(event)
    assert "BYPASS" in written

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


@allure.title("hook_mode: unset → legacy, junk → advisory, valid modes pass")
def test_hook_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_HOOK_MODE", raising=False)
    monkeypatch.setattr(advisory, "_hook_settings", lambda: None)
    assert advisory.hook_mode() == ""
    monkeypatch.setenv("GREEDY_HOOK_MODE", "bogus")
    assert advisory.hook_mode() == advisory.HOOK_MODE_ADVISORY
    for mode in sorted(advisory.HOOK_MODES):
        monkeypatch.setenv("GREEDY_HOOK_MODE", f" {mode.upper()} ")
        assert advisory.hook_mode() == mode


@allure.title("hook_mode: yaml config applies when env unset, env wins over config")
def test_hook_mode_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_HOOK_MODE", raising=False)
    cfg = SimpleNamespace(mode=advisory.HOOK_MODE_INTERCEPT, min_confidence=0.7)
    monkeypatch.setattr(advisory, "_hook_settings", lambda: cfg)
    assert advisory.hook_mode() == advisory.HOOK_MODE_INTERCEPT
    monkeypatch.setenv("GREEDY_HOOK_MODE", "gate")
    assert advisory.hook_mode() == advisory.HOOK_MODE_GATE

    empty = SimpleNamespace(mode=None, min_confidence=None)
    monkeypatch.delenv("GREEDY_HOOK_MODE", raising=False)
    monkeypatch.setattr(advisory, "_hook_settings", lambda: empty)
    assert advisory.hook_mode() == ""


@allure.title("hook_min_confidence: env override, enforce default, advisory lock")
def test_hook_min_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_HOOK_MODE", raising=False)
    monkeypatch.delenv("GREEDY_HOOK_MIN_CONFIDENCE", raising=False)
    monkeypatch.setattr(advisory, "_hook_settings", lambda: None)
    assert advisory.hook_min_confidence() == advisory.ADVISORY_MIN_CONFIDENCE

    # Legacy path: env alone arms the threshold when no mode is set.
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.9")
    assert advisory.hook_min_confidence() == 0.9
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "junk")
    assert advisory.hook_min_confidence() == advisory.ADVISORY_MIN_CONFIDENCE

    for mode in (advisory.HOOK_MODE_GATE, advisory.HOOK_MODE_INTERCEPT):
        monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
        monkeypatch.delenv("GREEDY_HOOK_MIN_CONFIDENCE", raising=False)
        assert advisory.hook_min_confidence() == advisory.ENFORCE_MIN_CONFIDENCE
        monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.7")
        assert advisory.hook_min_confidence() == 0.7

    # Advisory mode wins over an explicit low threshold — never blocks.
    monkeypatch.setenv("GREEDY_HOOK_MODE", "advisory")
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.1")
    assert advisory.hook_min_confidence() == advisory.ADVISORY_MIN_CONFIDENCE


@allure.title("effective_hook_mode: unset → advisory; legacy ≤1.0 threshold → intercept")
def test_effective_hook_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_HOOK_MODE", raising=False)
    monkeypatch.delenv("GREEDY_HOOK_MIN_CONFIDENCE", raising=False)
    monkeypatch.setattr(advisory, "_hook_settings", lambda: None)
    assert advisory.effective_hook_mode() == advisory.HOOK_MODE_ADVISORY

    # Legacy: no mode, but the threshold env still arms execute-and-block —
    # the savings claim is intercept-level.  1.0 is the documented boundary.
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.9")
    assert advisory.effective_hook_mode() == advisory.HOOK_MODE_INTERCEPT
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "1.0")
    assert advisory.effective_hook_mode() == advisory.HOOK_MODE_INTERCEPT
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "1.01")
    assert advisory.effective_hook_mode() == advisory.HOOK_MODE_ADVISORY
    monkeypatch.delenv("GREEDY_HOOK_MIN_CONFIDENCE", raising=False)

    # Explicit modes pass through unchanged.
    for mode in sorted(advisory.HOOK_MODES):
        monkeypatch.setenv("GREEDY_HOOK_MODE", mode)
        assert advisory.effective_hook_mode() == mode


@allure.title("format_gate_user_message: invoke pointer + bypass hint")
def test_format_gate_user_message() -> None:
    msg = advisory.format_gate_user_message(
        "what changed in recent commits", op_id="python-git-recent"
    )
    assert "python-git-recent" in msg
    assert "greedy-token capabilities invoke python-git-recent" in msg
    assert "cursor:" in msg

    ev = advisory.AdvisoryEvent(
        ts="t",
        kind=advisory.KIND_GATE,
        action="blocked",
        prompt="p",
        target="python",
        route_id="python-git-recent",
        confidence=0.7,
        est_tokens=0,
        blocked=True,
    )
    assert "GATE" in advisory.format_terminal_block(ev)


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


@allure.title("watch_events: partial final line is buffered until the newline arrives")
def test_watch_partial_line_buffered(
    advisory_log: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # An event written in two writes (split mid-record, no trailing newline in
    # the first write) must be emitted once — when the record completes, and
    # its Z-suffixed ISO timestamp must survive verbatim.
    row = {
        "ts": "2026-09-27T12:00:00Z",
        "kind": advisory.KIND_PASS,
        "action": "pass",
        "prompt": "split-write event",
        "target": "cursor",
        "route_id": "r",
        "confidence": 0.5,
        "est_tokens": 1,
    }
    payload = json.dumps(row) + "\n"
    cut = len(payload) // 2
    advisory_log.write_text(payload[:cut], encoding="utf-8")

    calls = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            with advisory_log.open("a", encoding="utf-8") as stream:
                stream.write(payload[cut:])
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(advisory.time, "sleep", fake_sleep)
    assert advisory.watch_events(follow=True, from_start=True, json_out=True) == 0
    out = capsys.readouterr().out
    assert "split-write event" in out
    assert out.count('"prompt"') == 1  # emitted once, never as fragments
    assert "2026-09-27T12:00:00Z" in out  # Z-suffixed ts retained


@allure.title("watch_events: incomplete tail is not emitted in once mode, complete rows are")
def test_watch_once_ignores_partial_tail(advisory_log: Path, capsys) -> None:
    row = {
        "ts": "2026-09-27T12:00:00Z",
        "kind": advisory.KIND_PASS,
        "action": "pass",
        "prompt": "complete row",
        "target": "cursor",
        "route_id": "r",
        "confidence": 0.5,
        "est_tokens": 1,
    }
    advisory_log.write_text(
        json.dumps(row) + "\n" + '{"unterminated', encoding="utf-8"
    )
    assert advisory.watch_events(follow=False, from_start=True, json_out=True) == 0
    out = capsys.readouterr().out
    assert "complete row" in out
    assert "unterminated" not in out


# ---------------------------------------------------------------------------
# Mutation-hardening: exact contracts for advisory helpers/formatters/watch.
# ---------------------------------------------------------------------------


@allure.title("advisory_enabled: off-words in any case disable; default stays on")
def test_advisory_enabled_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_ADVISORY", raising=False)
    assert advisory.advisory_enabled() is True
    for off in ("0", "false", "FALSE", " Off ", "no", "NO"):
        monkeypatch.setenv("GREEDY_ADVISORY", off)
        assert advisory.advisory_enabled() is False, off
    monkeypatch.setenv("GREEDY_ADVISORY", "junk")
    assert advisory.advisory_enabled() is True


@allure.title("overkill_gate_enabled: only explicit on-words enable")
def test_overkill_gate_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_OVERKILL_GATE", raising=False)
    assert advisory.overkill_gate_enabled() is False
    for on in ("1", "true", "TRUE", " yes ", "ON"):
        monkeypatch.setenv("GREEDY_OVERKILL_GATE", on)
        assert advisory.overkill_gate_enabled() is True, on
    monkeypatch.setenv("GREEDY_OVERKILL_GATE", "2")
    assert advisory.overkill_gate_enabled() is False


@allure.title("_utc_now_iso emits whole-second ISO-8601 with Z suffix")
def test_utc_now_iso_shape() -> None:
    import re

    ts = advisory._utc_now_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts)


@allure.title("_truncate keeps exactly-limit text verbatim")
def test_truncate_boundary() -> None:
    text = "x" * advisory.TASK_MAX_LEN
    assert advisory._truncate(text) == text  # <= limit → unchanged
    assert advisory._truncate(text + "y") == "x" * (advisory.TASK_MAX_LEN - 1) + "…"
    assert advisory._truncate("  pad  ") == "pad"


@allure.title("parse_attachments skips non-dict items but keeps scanning")
def test_parse_attachments_continues() -> None:
    data = {"attachments": ["junk", {"path": "after.py"}]}
    assert advisory.parse_attachments(data) == ["after.py"]
    assert advisory.parse_attachments({"attachments": [{"file_path": ""}, {"file_path": "b"}]}) == ["b"]
    assert advisory.parse_attachments({"attachments": None}) == []


@allure.title("is_overkill: fallback + attachments=0 stays under threshold")
def test_is_overkill_fallback_zero_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREEDY_OVERKILL_ATTACHMENTS", "3")
    # cursor-fallback with zero attachments → False (> 0 required)
    assert (
        advisory.is_overkill(
            "what is x", route_id="cursor-fallback",
            target="cursor", attachment_count=0,
        )
        is False
    )


@allure.title("overkill_recommendations emits the exact advice list")
def test_overkill_recommendations_golden() -> None:
    recs = advisory.overkill_recommendations(
        prompt="p", attachment_count=2, est_tokens=1234, route_id="cursor-fallback"
    )
    assert recs == [
        "Agent overkill (~1,234 tokens with rules context).",
        "Route: cursor-fallback.",
        "Attachments: 2 — открепите или pin 1–3 файла.",
        "Shift+Tab → Ask (вопрос без правок)",
        "Переформулировать: find … / объясни … → hook перехватит",
        "Префикс ask: — read-only в Agent",
        "Нужен полный Agent → cursor: <промпт>",
    ]


@allure.title("append_event writes one UTF-8 LF-terminated JSON line")
def test_append_event_bytes(
    advisory_log: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    nested = tmp_path / "deep" / "nested" / "advisory.jsonl"
    monkeypatch.setenv("GREEDY_ADVISORY_LOG", str(nested))
    monkeypatch.delenv("GREEDY_ADVISORY", raising=False)
    ev = advisory.AdvisoryEvent(
        ts="t", kind="k", action="a", prompt="привет", target="cursor",
        route_id="r", confidence=0.5, est_tokens=1,
    )
    advisory.append_event(ev)
    raw = nested.read_bytes()
    assert raw == (json.dumps(ev.to_dict(), ensure_ascii=False) + "\n").encode()
    assert "привет".encode() in raw  # non-ASCII must not be \u-escaped


@allure.title("append_event opens the log with utf-8 + pinned LF newline")
def test_append_event_open_args(
    advisory_log: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_open = Path.open
    seen: list[dict] = []

    def spy(self: Path, *args, **kwargs):
        if self == advisory_log:
            seen.append(kwargs)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    ev = advisory.AdvisoryEvent(
        ts="t", kind="k", action="a", prompt="p", target="cursor",
        route_id="r", confidence=0.5, est_tokens=1,
    )
    advisory.append_event(ev)
    assert seen and seen[-1]["encoding"] == "utf-8"
    assert seen[-1]["newline"] == ""


@allure.title("write_tty opens the tty with utf-8 encoding")
def test_write_tty_open_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tty = tmp_path / "tty.out"
    monkeypatch.setenv("GREEDY_TOKEN_TTY", str(tty))
    real_open = Path.open
    seen: list[dict] = []

    def spy(self: Path, *args, **kwargs):
        if self == tty:
            seen.append((args, kwargs))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    ev = advisory.AdvisoryEvent(
        ts="t", kind="k", action="a", prompt="p", target="cursor",
        route_id="r", confidence=0.5, est_tokens=1,
    )
    advisory.write_tty(ev)
    assert seen and seen[-1][1]["encoding"] == "utf-8"
    assert tty.read_text(encoding="utf-8") == advisory.format_terminal_block(ev)


@allure.title("build_event golden: every field attributed from decision+data")
def test_build_event_golden() -> None:
    data = {
        "attachments": [{"file_path": f"f{i}"} for i in range(10)],
        "session_id": "s1",
        "composer_mode": "agent",
    }
    ev = advisory.build_event(
        kind=advisory.KIND_GATE,
        action="blocked",
        prompt="probe",
        decision=_decision(
            target="ollama", route_id="ollama-x",
            confidence=0.62, est_tokens=4321,
        ),
        data=data,
        blocked=True,
        recommendations=["r1", "r2"],
    )
    d = ev.to_dict()
    assert d["kind"] == "gate" and d["action"] == "blocked"
    assert d["prompt"] == "probe"
    assert d["target"] == "ollama"
    assert d["route_id"] == "ollama-x"
    assert d["confidence"] == 0.62
    assert d["est_tokens"] == 4321
    assert d["attachment_count"] == 10
    assert d["attachments"] == [f"f{i}" for i in range(8)]  # capped at 8
    assert d["session_id"] == "s1"
    assert d["composer_mode"] == "agent"
    assert d["recommendations"] == ["r1", "r2"]
    assert d["blocked"] is True
    assert d["ts"].endswith("Z")


@allure.title("build_event defaults: missing decision attrs + empty data")
def test_build_event_defaults() -> None:
    ev = advisory.build_event(
        kind="k", action="a", prompt="p", decision=object(), data={}
    )
    assert ev.blocked is False
    assert ev.target == "cursor"
    assert ev.route_id == ""
    assert ev.confidence == 0.0
    assert ev.est_tokens == 0
    assert ev.attachments == []
    assert ev.composer_mode is None
    assert ev.recommendations == []
    assert ev.session_id is None


@allure.title("format_terminal_block renders the exact block")
def test_format_terminal_block_golden() -> None:
    ev = advisory.AdvisoryEvent(
        ts="t", kind=advisory.KIND_OVERKILL, action="warn", prompt="task text",
        target="cursor", route_id="cursor-fallback", confidence=0.4,
        est_tokens=9000, attachment_count=2, recommendations=["do x"],
        blocked=False,
    )
    block = advisory.format_terminal_block(ev)
    assert block == (
        "\n"
        "\033[36m[greedy-token watch]\033[0m OVERKILL (Agent heavy) · WARN\n"
        "  tier: CURSOR (cursor-fallback, 40%)\n"
        "  est: ~9,000 tokens\n"
        "  attachments: 2\n"
        "  prompt: task text\n"
        "\033[33m  recommendations:\033[0m\n"
        "    · do x\n"
    )
    for kind, header in (
        (advisory.KIND_INTERCEPT, "INTERCEPT (cheap tier)"),
        (advisory.KIND_PASS, "PASS (Agent)"),
        (advisory.KIND_BYPASS, "BYPASS (cursor: prefix)"),
        (advisory.KIND_GATE, "GATE (invoke required)"),
    ):
        e2 = advisory.AdvisoryEvent(
            ts="t", kind=kind, action="act", prompt="p", target="cursor",
            route_id="r", confidence=0.5, est_tokens=1,
        )
        assert f"{header} · ACT" in advisory.format_terminal_block(e2)
    blocked = advisory.AdvisoryEvent(
        ts="t", kind=advisory.KIND_PASS, action="pass", prompt="p",
        target="cursor", route_id="r", confidence=0.5, est_tokens=1,
        blocked=True,
    )
    assert "PASS (Agent) · BLOCKED" in advisory.format_terminal_block(blocked)


@allure.title("format_overkill_user_message renders the exact toast")
def test_format_overkill_user_message_golden() -> None:
    msg = advisory.format_overkill_user_message(
        "fix the thing", attachment_count=1, est_tokens=9000,
        route_id="cursor-fallback",
    )
    assert msg == (
        "greedy-token: Agent overkill — отправка остановлена\n\n"
        "Задача: fix the thing\n\n"
        "· Agent overkill (~9,000 tokens with rules context).\n"
        "· Route: cursor-fallback.\n"
        "· Attachments: 1 — открепите или pin 1–3 файла.\n"
        "· Shift+Tab → Ask (вопрос без правок)\n"
        "· Переформулировать: find … / объясни … → hook перехватит\n"
        "· Префикс ask: — read-only в Agent\n"
        "· Нужен полный Agent → cursor: <промпт>\n\n"
        "---\n"
        "Agent всё равно нужен → cursor: <промпт>"
    )


@allure.title("format_gate_user_message renders the exact toast")
def test_format_gate_user_message_golden() -> None:
    msg = advisory.format_gate_user_message(
        "what changed", op_id="python-git-recent"
    )
    assert msg == (
        "greedy-token gate — детерминированный op, отправка остановлена\n\n"
        "Задача: what changed\n"
        "Op: python-git-recent (ready · read-only)\n\n"
        "Запуск: greedy-token capabilities invoke python-git-recent · "
        "MCP: greedy_token_invoke\n"
        "---\n"
        "Agent всё равно нужен → cursor: <промпт>"
    )


@allure.title("event_from_dict golden: full row and missing-keys defaults")
def test_event_from_dict_golden() -> None:
    row = {
        "ts": "2026-01-01T00:00:00Z", "kind": "gate", "action": "blocked",
        "prompt": "p", "target": "ollama", "route_id": "r1",
        "confidence": 0.75, "est_tokens": 42, "attachment_count": 3,
        "attachments": ["a", "b"], "session_id": "s",
        "composer_mode": "agent", "recommendations": ["x"],
        "blocked": True,
    }
    ev = advisory.event_from_dict(row)
    assert ev.to_dict() == {
        "ts": "2026-01-01T00:00:00Z", "kind": "gate", "action": "blocked",
        "prompt": "p", "target": "ollama", "route_id": "r1",
        "confidence": 0.75, "est_tokens": 42, "attachment_count": 3,
        "attachments": ["a", "b"], "session_id": "s",
        "composer_mode": "agent", "recommendations": ["x"],
        "blocked": True,
    }
    ev2 = advisory.event_from_dict({})
    assert ev2.to_dict() == {
        "ts": "", "kind": "", "action": "", "prompt": "", "target": "",
        "route_id": "", "confidence": 0.0, "est_tokens": 0,
        "attachment_count": 0, "attachments": [], "session_id": None,
        "composer_mode": None, "recommendations": [], "blocked": False,
    }
    # falsy-but-present values fall back the same as missing keys
    ev3 = advisory.event_from_dict(
        {"attachments": None, "recommendations": 0}
    )
    assert ev3.attachments == [] and ev3.recommendations == []


@allure.title("watch_events announces + creates a missing log on stderr")
def test_watch_missing_log_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tmp_path / "deep" / "nested" / "a.jsonl"
    monkeypatch.setenv("GREEDY_ADVISORY_LOG", str(log))
    assert advisory.watch_events(follow=False) == 0
    err = capsys.readouterr().err
    assert err == f"Waiting for advisory log: {log}\n"
    assert log.is_file()


@allure.title("watch_events once-mode emits nothing for already-read content")
def test_watch_from_size_no_output(advisory_log: Path, capsys) -> None:
    advisory_log.write_text('{"kind": "pass"}\n', encoding="utf-8")
    assert advisory.watch_events(follow=False, json_out=True) == 0
    assert capsys.readouterr().out == ""


@allure.title("watch_events truncation reset re-reads from byte 0 with empty pending")
def test_watch_truncation_replay(
    advisory_log: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    row = {"kind": "pass", "action": "p", "prompt": "after-truncate",
           "target": "cursor", "route_id": "r", "confidence": 0.5,
           "est_tokens": 1, "ts": "t"}
    advisory_log.write_text(
        json.dumps({"pad": "x" * 500}) + "\n", encoding="utf-8"
    )
    calls = {"n": 0}
    short = json.dumps(row) + "\n"

    def fake_sleep(seconds: float) -> None:
        assert seconds == 0.25
        calls["n"] += 1
        if calls["n"] == 1:
            advisory_log.write_text(short, encoding="utf-8")
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(advisory.time, "sleep", fake_sleep)
    assert advisory.watch_events(follow=True, from_start=False, json_out=True) == 0
    captured = capsys.readouterr()
    assert "after-truncate" in captured.out  # reset to byte 0, no stale pending
    assert f"watching {advisory_log}" in captured.err
    assert captured.err.endswith("\n\033[90mwatch stopped\033[0m\n")


@allure.title("watch_events decodes invalid bytes as U+FFFD, skips bad JSON")
def test_watch_bad_bytes(
    advisory_log: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    advisory_log.write_bytes(
        b'\xff\xfe{bad\n{"kind": "pass", "action": "p", "prompt": "ok-row",'
        b' "target": "c", "route_id": "r", "confidence": 0.1,'
        b' "est_tokens": 1, "ts": "t"}\n'
    )
    assert advisory.watch_events(follow=False, from_start=True, json_out=True) == 0
    out = capsys.readouterr().out
    assert "ok-row" in out


@allure.title("watch_events skips empty and non-JSON lines mid-stream")
def test_watch_skips_junk_mid_stream(
    advisory_log: Path, capsys
) -> None:
    advisory_log.write_text(
        '\nnot-json\n{"kind": "pass", "action": "p", "prompt": "survivor",'
        ' "target": "c", "route_id": "r", "confidence": 0.1,'
        ' "est_tokens": 1, "ts": "t"}\n',
        encoding="utf-8",
    )
    assert advisory.watch_events(follow=False, from_start=True, json_out=True) == 0
    out = capsys.readouterr().out
    assert "survivor" in out
    assert "not-json" not in out


@allure.title("watch_events json_out keeps non-ASCII unescaped")
def test_watch_json_out_unicode(
    advisory_log: Path, capsys
) -> None:
    advisory_log.write_text(
        json.dumps({"kind": "pass", "action": "p", "prompt": "привет",
                    "target": "c", "route_id": "r", "confidence": 0.1,
                    "est_tokens": 1, "ts": "t"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    assert advisory.watch_events(follow=False, from_start=True, json_out=True) == 0
    assert "привет" in capsys.readouterr().out


@allure.title("watch_events bare call defaults to follow mode")
def test_watch_events_default_follows(
    advisory_log: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    advisory_log.write_text(
        '{"kind": "pass", "action": "p", "prompt": "x", "target": "c",'
        ' "route_id": "r", "confidence": 0.1, "est_tokens": 1, "ts": "t"}\n',
        encoding="utf-8",
    )

    def fake_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(advisory.time, "sleep", fake_sleep)
    # bare call: follow must default to True → the watching banner appears
    assert advisory.watch_events() == 0
    assert "watching" in capsys.readouterr().err


@allure.title("watch_events defaults to human-readable (not json) output")
def test_watch_events_default_text_out(
    advisory_log: Path, capsys
) -> None:
    advisory_log.write_text(
        '{"kind": "pass", "action": "p", "prompt": "x", "target": "cursor",'
        ' "route_id": "r", "confidence": 0.1, "est_tokens": 1, "ts": "t"}\n',
        encoding="utf-8",
    )
    # json_out omitted → text block, not a JSON object dump
    assert advisory.watch_events(follow=False, from_start=True) == 0
    out = capsys.readouterr().out
    assert "\033[36m[greedy-token watch]" in out
    assert '"kind"' not in out


@allure.title("render_result_output passes through non-JSON stdout")
def test_render_result_output_plain_text() -> None:
    raw = "3 repos found\nfoo\nbar"
    assert advisory.render_result_output(raw) == raw


@allure.title("render_result_output renders dict scalars and object array as table")
def test_render_result_output_dict_table() -> None:
    raw = json.dumps(
        {
            "ok": True,
            "repo": ".",
            "count": 2,
            "commits": [
                {"sha": "abc1234", "subject": "first"},
                {"sha": "def5678", "subject": "second", "extra": "x"},
            ],
        }
    )
    out = advisory.render_result_output(raw)
    assert "**repo**: ." in out
    assert "**count**: 2" in out
    assert "| sha | subject | extra |" in out
    assert "| abc1234 | first |  |" in out


@allure.title("render_result_output renders a bare list of dicts as a table")
def test_render_result_output_top_list() -> None:
    raw = json.dumps([{"a": 1}, {"a": 2}])
    out = advisory.render_result_output(raw)
    assert out.startswith("| a |")
    assert "| 2 |" in out


@allure.title("render_result_output caps rows and truncates wide cells")
def test_render_result_output_truncation() -> None:
    rows = [{"c": "x" * 200} for _ in range(15)]
    out = advisory.render_result_output(json.dumps(rows))
    assert "+5 more" in out
    assert "x" * 72 not in out
    assert "…" in out


@allure.title("render_result_output escapes pipes and newlines inside cells")
def test_render_result_output_cell_sanitization() -> None:
    out = advisory.render_result_output(
        json.dumps([{"m": "a|b\nc"}])
    )
    assert "a\\|b c" in out
    assert "\nc" not in out.splitlines()[2]


@allure.title("render_result_output keeps empty and scalar JSON unchanged")
def test_render_result_output_passthrough_edge() -> None:
    assert advisory.render_result_output("") == ""
    assert advisory.render_result_output("42") == "42"


@allure.title("render_result_output keeps non-tabular JSON unchanged")
def test_render_result_output_non_tabular_json() -> None:
    for raw in ("[]", '["a", "b"]', "{}", '"just a string"'):
        assert advisory.render_result_output(raw) == raw


@allure.title("render_result_output renders nested dict and scalar list inline")
def test_render_result_output_nested_values() -> None:
    out = advisory.render_result_output(
        json.dumps({"meta": {"elapsed": 1}, "tags": ["a", "b"]})
    )
    assert "**meta**: elapsed=1" in out
    assert "**tags**: a, b" in out


@allure.title("render_result_output expands deep dicts instead of truncated JSON")
def test_render_result_output_deep_dict() -> None:
    out = advisory.render_result_output(
        json.dumps(
            {
                "app": {
                    "procs": 43,
                    "cpu": 145.4,
                    "by_role": {
                        "main": {"procs": 14, "cpu": 6.6},
                        "gpu": {"procs": 1, "cpu": 16.2},
                    },
                }
            }
        )
    )
    assert "**app**" in out
    assert "- procs: 43" in out
    assert "  - main: procs=14, cpu=6.6" in out
    assert '{"procs"' not in out


@allure.title("render_result_output inlines list cells with +N overflow")
def test_render_result_output_list_cell() -> None:
    row = {"name": "x", "files": ["a", "b", "c", "d", "e"]}
    out = advisory.render_result_output(json.dumps([row]))
    assert "a, b, c, d +1" in out


@allure.title("render_result_output embeds nested dict cell as compact json")
def test_render_result_output_dict_cell() -> None:
    out = advisory.render_result_output(
        json.dumps([{"stats": {"add": 3}}])
    )
    assert '{"add": 3}' in out


@allure.title("hook_min_confidence: yaml threshold applies when env unset")
def test_hook_min_confidence_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_HOOK_MIN_CONFIDENCE", raising=False)
    monkeypatch.setenv("GREEDY_HOOK_MODE", "intercept")
    cfg = SimpleNamespace(mode="intercept", min_confidence=0.7)
    monkeypatch.setattr(advisory, "_hook_settings", lambda: cfg)
    assert advisory.hook_min_confidence() == 0.7

    # env still beats the yaml threshold
    monkeypatch.setenv("GREEDY_HOOK_MIN_CONFIDENCE", "0.9")
    assert advisory.hook_min_confidence() == 0.9


@allure.title("_hook_settings: get_hook_settings failure → unconfigured")
def test_hook_settings_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import settings as _settings

    monkeypatch.setattr(
        _settings, "get_hook_settings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert advisory._hook_settings() is None
