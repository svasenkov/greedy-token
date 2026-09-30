from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

import allure
from greedy_token import hook_host
from greedy_token.hook_host import HostProfile, detect

pytestmark = [
    allure.epic("Greedy token"),
    allure.parent_suite("Greedy token"),
    allure.feature("Hook hosts"),
    allure.suite("Hook hosts"),
]


@pytest.mark.parametrize("data", [{}, {"prompt": "hello"}, {"client": "other"}])
def test_default_profile_is_cursor_compatible(data):
    profile = detect(data)
    assert isinstance(profile, HostProfile)
    assert profile.name == "cursor"
    assert profile.supported
    assert not profile.can_render_full
    assert profile.link_style == "file_uri"
    assert not profile.supports_context_injection
    assert not profile.soft_gate_enabled


def test_devin_profile_capabilities(monkeypatch):
    monkeypatch.delenv("GREEDY_DEVIN_SOFT_GATE", raising=False)
    profile = detect({"hook_event_name": "UserPromptSubmit"})
    assert profile.name == "devin"
    assert profile.supported
    assert profile.can_render_full
    assert profile.link_style == "ref_chip"
    assert profile.supports_context_injection
    assert not profile.soft_gate_enabled


@pytest.mark.parametrize("data", [
    None, [], "prompt", 1,
    {"hook_event_name": "PreToolUse"},
    {"hook_event_name": "UnknownPromptSubmit"},
    {"hook_event_name": None},
    {"hook_event_name": ""},
])
def test_unknown_profile_fails_open_without_spilling(data, tmp_path):
    profile = detect(data)
    assert profile.name == "unknown"
    assert not profile.supported
    assert profile.link_style == "plain_path"
    assert not profile.can_render_full
    assert not profile.supports_context_injection
    assert not profile.soft_gate_enabled
    assert profile.parse_prompt(data) == ""
    assert profile.serialize_pass() == "{}"
    assert profile.serialize_gate({"user_message": "stop", "op_id": "op"}) == "{}"
    assert profile.serialize_intercept({"root": tmp_path}) == "{}"
    assert not (tmp_path / ".greedy-token").exists()


@pytest.mark.parametrize("event", [None, "UserPromptSubmit"], ids=["cursor", "devin"])
@pytest.mark.parametrize("prompt,expected", [
    ("  why slow cpu  ", "why slow cpu"), (None, ""), ("", ""),
    (1, ""), ([], ""), ({"text": "hello"}, ""),
])
def test_parse_prompt_handles_absent_and_invalid_values(event, prompt, expected):
    data = {"prompt": prompt}
    if event:
        data["hook_event_name"] = event
    assert detect(data).parse_prompt(data) == expected


def test_injected_blocks_are_removed_only_for_devin():
    text = (
        '<system_info>context</system_info>\n'
        '<project_context name="session">literal </project_context> in summary'
        '</project_context>\n'
        '<rules type="always-on">rules</rules>\n'
        '  why slow cpu  '
    )
    data = {"prompt": text}
    assert detect(data).parse_prompt(data) == text.strip()
    data["hook_event_name"] = "UserPromptSubmit"
    assert detect(data).parse_prompt(data) == "why slow cpu"


@pytest.mark.parametrize("tag", [
    "project_context", "system_info", "additional_metadata", "rules",
    "available_rules", "available_skills",
])
def test_each_injected_tag_is_stripped(tag):
    assert hook_host.strip_injected_context(f"<{tag}>blob</{tag}>\nrequest") == "request"


@pytest.mark.parametrize("text", [
    "<project_context>unclosed", "<project_context>blob</project_context>",
    "user text <project_context>blob</project_context>", "   ",
])
def test_injected_context_fallback_preserves_input(text):
    assert hook_host.strip_injected_context(text) == text


@pytest.mark.parametrize("prefix", hook_host.BYPASS_PREFIXES)
def test_bypass_prefix_vocabulary_is_preserved(prefix):
    assert prefix in ("cursor:", "agent:", "gt-skip:", "nogreedy:")


@pytest.mark.parametrize("prompt,expected,ask", [
    ("ASK:  request", "request", True), ("?request", "request", True),
    ("ask:", "ask:", True), ("?", "?", True),
    ("ordinary request", "ordinary request", False),
])
def test_ask_prefix_normalization(prompt, expected, ask):
    assert hook_host.strip_ask_prefix(prompt) == (expected, ask)


@pytest.mark.parametrize("devin", [False, True], ids=["cursor", "devin"])
def test_pass_and_gate_serialization_are_exact(devin, monkeypatch):
    monkeypatch.setenv("GREEDY_DEVIN_SOFT_GATE", "0")
    data = {"hook_event_name": "UserPromptSubmit"} if devin else {}
    profile = detect(data)
    assert profile.serialize_pass() == ("{}" if devin else '{"continue": true}')
    payload = {"continue": False, "user_message": "Остановлено"}
    expected = {"decision": "block", "reason": "Остановлено"} if devin else payload
    assert profile.serialize_gate(payload) == json.dumps(expected, ensure_ascii=False)
    assert profile.serialize("gate", payload) == profile.serialize_gate(payload)
    assert profile.serialize("pass", {}) == profile.serialize_pass()
    assert profile.serialize("unknown", payload) == profile.serialize_pass()


def test_devin_encoder_default_reason():
    profile = detect({"hook_event_name": "UserPromptSubmit"})
    assert profile.serialize_pass({"continue": False}) == (
        '{"decision": "block", "reason": "greedy-token"}'
    )
    assert profile.serialize_pass({"continue": True, "user_message": "ignored"}) == "{}"


@pytest.mark.parametrize("raw,enabled", [
    (None, False), ("", False), ("0", False), ("false", False), ("junk", False),
    ("1", True), ("true", True), ("yes", True), ("on", True), (" TRUE ", True),
])
def test_soft_gate_is_profile_scoped(raw, enabled, monkeypatch):
    if raw is None:
        monkeypatch.delenv("GREEDY_DEVIN_SOFT_GATE", raising=False)
    else:
        monkeypatch.setenv("GREEDY_DEVIN_SOFT_GATE", raw)
    cursor = detect({})
    devin = detect({"hook_event_name": "UserPromptSubmit"})
    unknown = detect({"hook_event_name": "unknown"})
    assert not cursor.soft_gate_enabled
    assert devin.soft_gate_enabled is enabled
    assert not unknown.soft_gate_enabled
    hard = {"continue": False, "user_message": "stop"}
    assert cursor.serialize_gate(hard) == json.dumps(hard)
    assert devin.serialize_gate(hard) == '{"decision": "block", "reason": "stop"}'
    if enabled:
        expected = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": hook_host.format_devin_gate_context("python-check"),
            },
        }
        assert devin.serialize_gate({"op_id": "python-check"}) == json.dumps(
            expected, ensure_ascii=False,
        )


def test_profile_resolution_does_not_leak_between_submissions(monkeypatch):
    monkeypatch.setenv("GREEDY_DEVIN_SOFT_GATE", "1")
    soft = detect({"hook_event_name": "UserPromptSubmit"})
    monkeypatch.setenv("GREEDY_DEVIN_SOFT_GATE", "0")
    hard = detect({"hook_event_name": "UserPromptSubmit"})
    assert soft.soft_gate_enabled
    assert not hard.soft_gate_enabled
    assert not detect({}).soft_gate_enabled


@pytest.mark.parametrize("devin", [False, True], ids=["cursor", "devin"])
def test_intercept_serialization_and_spill_are_exact(devin, tmp_path):
    profile = detect({"hook_event_name": "UserPromptSubmit"} if devin else {})
    body = "one\ntwo"
    payload = {"target": "PYTHON", "prompt": "why slow cpu", "body": body, "root": tmp_path}
    spill = tmp_path / ".greedy-token" / "last-intercept.md"
    message = "greedy-token → PYTHON · AI пропущен\n\nQ: why slow cpu"
    if devin:
        message += f'\n\none\ntwo\n\nПолный ответ → <ref_file file="{spill}" />'
    else:
        message += f"\nA: one\ntwo\n\nПолный ответ → [{spill.name}]({spill.as_uri()})"
    message += "\nЧтобы запустить без greedy — начните промпт с `nogreedy:`"
    expected = (
        {"decision": "block", "reason": message}
        if devin else {"continue": False, "user_message": message}
    )
    assert profile.serialize("intercept", payload) == json.dumps(expected, ensure_ascii=False)
    assert spill.read_text(encoding="utf-8") == (
        "# greedy-token → PYTHON\n\n## Задача\n\nwhy slow cpu\n\n## Ответ\n\none\ntwo\n"
    )


@pytest.mark.parametrize("full", [False, True])
def test_pretty_output_preserves_raw_spill_and_hit_files(full, tmp_path):
    profile = replace(detect({}), can_render_full=full, link_style="plain_path")
    body = "src/example.py:10:value\nsrc/other.py:2:value"
    pretty = "human readable answer"
    message = json.loads(profile.serialize_intercept({
        "target": "TOOL", "prompt": "find value", "body": body,
        "pretty": pretty, "root": tmp_path,
    }))["user_message"]
    assert pretty in message
    assert "files: src/example.py, src/other.py" in message
    spill = tmp_path / ".greedy-token" / "last-intercept.md"
    assert f"Полный ответ → {spill}" in message
    assert "file://" not in message
    assert "<ref_file" not in message
    assert spill.read_text(encoding="utf-8").endswith(
        f"\n\n## Raw output\n\n```json\n{body}\n```\n"
    )


def test_default_spill_path(monkeypatch, tmp_path):
    spill = tmp_path / "answer.md"
    monkeypatch.setattr(hook_host, "INTERCEPT_SPILL", spill)
    assert hook_host.spill_intercept("TOOL", "request", "body", "body") == spill
    assert spill.is_file()


@pytest.mark.parametrize("full", [False, True])
def test_default_link_style_matches_legacy_full_flag(full, tmp_path):
    message = hook_host.format_user_message("PYTHON", "request", "body", full=full, root=tmp_path)
    assert ("<ref_file" in message) is full
    assert ("file://" in message) is not full


def test_preview_boundaries():
    assert hook_host._preview(" one\n two ", 7) == "one two"
    assert hook_host._preview("one two three", 8) == "one two…"
    assert hook_host._preview_lines("one\ntwo", 7) == "one\ntwo"
    assert hook_host._preview_lines("one\ntwo three", 8) == "one\ntwo\n…"


def test_hit_files_are_unique_and_bounded():
    body = "noise\na.py:1:value\na.py:2:other\nb.py:3:value\nc.py:4:value"
    assert hook_host.extract_hit_files(body, limit=2) == ["a.py", "b.py"]
    assert hook_host.extract_hit_files(body) == ["a.py", "b.py", "c.py"]


def test_hit_file_header_guard(monkeypatch):
    monkeypatch.setattr(hook_host, "_HIT_PATH_RE", re.compile(r"^(.+):(\d+):"))
    assert hook_host.extract_hit_files("Search:a.py:1:text\n---b.py:2:text\nc.py:3:text") == ["c.py"]


def test_custom_profile_controls_parsing_protocol_and_context(tmp_path):
    profile = replace(
        detect({}), name="custom", can_render_full=True, link_style="plain_path",
        _parser=lambda data: data["text"].strip(),
        _encoder=lambda payload: json.dumps({
            "halt": not payload["continue"], "text": payload.get("user_message", ""),
        }),
        _context_encoder=lambda context: json.dumps({"context": context}),
        soft_gate_enabled=True,
    )
    assert profile.parse_prompt({"text": "  custom request  "}) == "custom request"
    assert profile.supports_context_injection
    assert json.loads(profile.serialize_pass()) == {"halt": False, "text": ""}
    assert json.loads(profile.serialize_gate({"op_id": "custom-op"})) == {
        "context": hook_host.format_devin_gate_context("custom-op"),
    }
    assert json.loads(profile.serialize_gate({"user_message": "hard block"})) == {
        "halt": True, "text": "hard block",
    }
    payload = json.loads(profile.serialize_intercept({
        "target": "PYTHON", "prompt": "custom request", "body": "answer", "root": tmp_path,
    }))
    assert payload["halt"]
    assert "Полный ответ → " + str(tmp_path / ".greedy-token" / "last-intercept.md") in payload["text"]
    assert "file://" not in payload["text"]
    assert "<ref_file" not in payload["text"]
