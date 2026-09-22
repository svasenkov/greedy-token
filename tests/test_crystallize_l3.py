"""Crystallization L3 safe mode: draft → shadow route → promote / reject."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import allure
import pytest
import yaml

import greedy_token.cli as cli
import greedy_token.crystallize_l3 as l3
from greedy_token.crystal_ids import crystal_id_for_pattern
from greedy_token.hub.crystallize import crystal_timeline, list_crystals, load_lifecycle_events
from greedy_token.paths import remove_workspace_route, workspace_config_routes
from greedy_token.router import route_task
from greedy_token.scripts_lint import lint_routes
from tests.allure_reporting import attach_text

from tests.conftest import _seed_crystal_candidate as _seed_candidate

pytestmark = [
    allure.epic("Crystallize"),
    allure.parent_suite("Crystallize"),
    allure.feature("L3 safe mode"),
    allure.suite("Crystallize L3"),
]

TASK = "summarize weekly spend report table"
CRYSTAL_ID = crystal_id_for_pattern(TASK)


def _ns(**kwargs) -> Namespace:
    defaults = {"no_log": True, "json": False, "since": "30d", "by": "", "reason": ""}
    defaults.update(kwargs)
    return Namespace(**defaults)


# crystal_home and no_cheap_llm fixtures live in tests/conftest.py


# ---------------------------------------------------------------- draft


@allure.story("Draft")
@allure.title("draft without LLM: deterministic template skeleton + shadow route")
def test_draft_template_no_llm(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    result = l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    attach_text("draft", result.draft_path.read_text(encoding="utf-8"))

    assert result.source == "template"
    assert result.lint_ok, result.lint_violations
    code = result.draft_path.read_text(encoding="utf-8")
    assert f"Pattern: {TASK}" in code
    assert "Hits:    5" in code
    assert "TODO" in code
    assert "argparse" in code
    compile(code, str(result.draft_path), "exec")

    routes = workspace_config_routes(minimal_workspace)
    route = next(r for r in routes if r["id"] == CRYSTAL_ID)
    assert route["target"] == "python"
    assert route["enabled"] is False
    assert route["shadow_until"] == result.shadow_until
    assert route["patterns"] == [TASK]
    assert route["command"].endswith(f"drafts/{CRYSTAL_ID}.py")


@allure.story("Draft")
@allure.title("draft rejects script-{slugify(prompt)} as promote id")
def test_draft_rejects_prompt_slug_id(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    with pytest.raises(ValueError, match="script- prefix"):
        l3.draft_crystal("script-summarize-weekly-spend-report-table", root=minimal_workspace)


@allure.story("Draft")
@allure.title("draft with ollama stub: cheap_llm-generated body")
def test_draft_with_ollama_stub(
    minimal_workspace: Path, crystal_home: Path, ollama_stub: str
) -> None:
    result = l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    attach_text("draft", result.draft_path.read_text(encoding="utf-8"))
    assert result.source == "cheap_llm"
    assert "Source:  cheap_llm" in result.draft_path.read_text(encoding="utf-8")


@allure.story("Draft")
@allure.title("draft passes the existing scripts lint (route + script on disk)")
def test_draft_passes_scripts_lint(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    routes = workspace_config_routes(minimal_workspace)
    result = lint_routes(root=minimal_workspace, routes=routes)
    attach_text("lint", json.dumps(result))
    assert result["ok"], result["violations"]


@allure.story("Draft")
@allure.title("draft rejects a valid-looking id that is the truncated slug of a longer pattern")
def test_draft_rejects_truncated_slug_pattern(
    minimal_workspace: Path, crystal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dds = "d" * 39
    cid = f"python-aa-bb-cc-{dds}"
    pattern = f"aa bb cc {dds} extra"

    def fake_find(crystal_id: str, *, since: str | None = "30d") -> dict:
        return {"crystal_id": cid, "pattern": pattern, "hits": 5}

    monkeypatch.setattr(l3, "find_crystal", fake_find)
    with pytest.raises(ValueError, match="slugify"):
        l3.draft_crystal(cid, root=minimal_workspace)


@allure.story("Draft")
@allure.title("draft: unknown crystal id raises ValueError")
def test_draft_unknown_crystal(minimal_workspace: Path, crystal_home: Path) -> None:
    with pytest.raises(ValueError, match="not found in candidates"):
        l3.draft_crystal("python-no-such-crystal", root=minimal_workspace)


@allure.story("Draft")
@allure.title("draft: forbidden pattern (creative stem) is refused by lint")
def test_draft_forbidden_pattern(
    minimal_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "gt-home2"
    home.mkdir()
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    log = home / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(log))
    _seed_candidate(log, task="refactor the whole payment module")
    with pytest.raises(ValueError, match="fails scripts lint"):
        l3.draft_crystal(
            crystal_id_for_pattern("refactor the whole payment module"),
            root=minimal_workspace,
        )


# ---------------------------------------------------------------- shadow


@allure.story("Shadow")
@allure.title("shadow route never affects route_task — log-only match")
def test_shadow_route_does_not_affect_route_task(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    before = route_task(TASK, minimal_workspace)
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    after = route_task(TASK, minimal_workspace)

    assert after.target == before.target
    assert after.route_id == before.route_id
    assert after.route_id != CRYSTAL_ID
    # ... but the potential match is logged on the decision.
    assert after.shadow_route_id == CRYSTAL_ID


# ---------------------------------------------------------------- promote / reject


@allure.story("Promote")
@allure.title("promote flips approved → applied and route_task selects the crystal")
def test_promote_activates_route(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    result = l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    attach_text("promote", json.dumps(result))

    route = next(
        r for r in workspace_config_routes(minimal_workspace) if r["id"] == CRYSTAL_ID
    )
    assert "shadow_until" not in route
    assert "enabled" not in route

    decision = route_task(TASK, minimal_workspace)
    assert decision.route_id == CRYSTAL_ID
    assert decision.target == "python"


@allure.story("Promote")
@allure.title("promote: no proposal → unapproved → already applied errors")
def test_promote_errors(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    with pytest.raises(ValueError, match="no proposal"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    with pytest.raises(ValueError, match="not approved"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    with pytest.raises(ValueError, match="already applied"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)


@allure.story("Reject")
@allure.title("reject removes the draft script and the route")
def test_reject_cleans_draft_and_route(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    result = l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    rejected = l3.reject_crystal(CRYSTAL_ID, root=minimal_workspace)
    attach_text("reject", json.dumps(rejected))

    assert rejected["removed_route"] is True
    assert rejected["removed_draft"] is True
    assert not result.draft_path.exists()
    assert all(r["id"] != CRYSTAL_ID for r in workspace_config_routes(minimal_workspace))


@allure.story("Reject")
@allure.title("reject of an unknown crystal is a no-op (nothing removed)")
def test_reject_unknown_crystal(minimal_workspace: Path, crystal_home: Path) -> None:
    rejected = l3.reject_crystal("script-no-such-crystal", root=minimal_workspace)
    assert rejected == {
        "ok": True,
        "crystal_id": "script-no-such-crystal",
        "removed_route": False,
        "removed_draft": False,
        "revoked_trust": False,
    }


@allure.story("Reject")
@allure.title("reject skips unrelated workspace routes when scanning by id")
def test_reject_skips_unrelated_routes(
    tmp_path: Path, crystal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws-unrelated"
    root.mkdir()
    (root / ".greedy-token.yaml").write_text(
        yaml.safe_dump({"routes": [{"id": "python-other", "target": "python"}]}),
        encoding="utf-8",
    )
    rejected = l3.reject_crystal("script-no-such-crystal", root=root)
    assert rejected["removed_route"] is False
    # The unrelated route survives untouched.
    assert [r["id"] for r in workspace_config_routes(root)] == ["python-other"]


# ---------------------------------------------------------------- lifecycle / hub


@allure.story("Lifecycle")
@allure.title("draft → shadow → approved → promoted events land in the log and hub")
def test_lifecycle_events_promote_path(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)

    stages = [e["stage"] for e in load_lifecycle_events() if e["crystal_id"] == CRYSTAL_ID]
    assert stages == ["draft", "shadow", "approved", "promoted"]

    timeline = crystal_timeline(CRYSTAL_ID)
    assert timeline["latest_stage"] == "promoted"
    assert timeline["state"] == "applied"
    assert set(timeline["stages"]) == {"draft", "shadow", "approved", "promoted"}

    listing = list_crystals(since="30d")
    entry = next(c for c in listing["crystals"] if c["crystal_id"] == CRYSTAL_ID)
    assert entry["latest_stage"] == "promoted"
    assert entry["status"] == "active"
    assert entry["state"] == "applied"


@allure.story("Lifecycle")
@allure.title("append_lifecycle_event without extra writes the bare stage event")
def test_append_lifecycle_event_no_extra(crystal_home: Path) -> None:
    from greedy_token.hub.crystallize import append_lifecycle_event

    event = append_lifecycle_event(stage="draft", crystal_id="script-bare")
    assert event["stage"] == "draft"
    assert event["pattern"] == ""
    rows = load_lifecycle_events()
    assert rows[-1]["crystal_id"] == "script-bare"


@allure.story("Lifecycle")
@allure.title("reject path writes the rejected stage")
def test_lifecycle_events_reject_path(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.reject_crystal(CRYSTAL_ID, root=minimal_workspace)
    timeline = crystal_timeline(CRYSTAL_ID)
    assert timeline["latest_stage"] == "rejected"
    assert timeline["stages"]["rejected"]["status"] == "rejected"


# ---------------------------------------------------------------- code generation


@allure.story("Codegen")
@allure.title("extract_python_code: fenced, raw, empty, and non-compiling replies")
def test_extract_python_code() -> None:
    fenced = "prose\n```python\nprint('hi')\n```\nmore prose"
    assert l3.extract_python_code(fenced) == "print('hi')\n"
    assert l3.extract_python_code("x = 1") == "x = 1\n"
    assert l3.extract_python_code("   ") is None
    assert l3.extract_python_code("def broken(:") is None


@allure.story("Codegen")
@allure.title("generate_draft_code falls back to template when LLM errors or emits junk")
def test_generate_draft_code_llm_fallbacks(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("greedy_token.cheap_llm.cheap_llm_available", lambda settings: True)

    def _raise(*args, **kwargs):
        raise OSError("connection refused")

    # The draft path calls providers via llm_invoke.llm_chat (guarded) — mock
    # at that seam, not at cheap_llm_chat which is no longer invoked here.
    monkeypatch.setattr("greedy_token.llm_invoke.llm_chat", _raise)
    code, source = l3.generate_draft_code("script-x", "pattern text", 3, root=minimal_workspace)
    assert source == "template"

    monkeypatch.setattr(
        "greedy_token.llm_invoke.llm_chat",
        lambda *args, **kwargs: ("def broken(:", None),
    )
    code, source = l3.generate_draft_code("script-x", "pattern text", 3, root=minimal_workspace)
    assert source == "template"
    assert "TODO" in code


@allure.story("Codegen")
@allure.title("draft generation goes through the guarded invoke path — metered denial makes no provider call")
def test_generate_draft_code_metered_denied_no_http(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = {
        "llm": {
            "cheap": {
                "models": [{
                    "id": "bulk", "enabled": True, "model": "bulk-m",
                    "profiles": ["*"], "billing": "metered", "cost_per_1m_usd": 0.1,
                }]
            },
            "escalation": {"enabled": False},
        }
    }
    (minimal_workspace / ".greedy-token.yaml").write_text(
        yaml.safe_dump(cfg), encoding="utf-8"
    )
    monkeypatch.setattr("greedy_token.cheap_llm.cheap_llm_available", lambda settings: True)

    from greedy_token import llm_invoke

    calls: list = []
    monkeypatch.setattr(
        llm_invoke, "llm_chat", lambda *a, **k: calls.append((a, k)) or ("x", 1)
    )
    code, source = l3.generate_draft_code("script-x", "pattern text", 3, root=minimal_workspace)
    assert source == "template"
    assert calls == []
    assert "TODO" in code


# ---------------------------------------------------------------- workspace route helpers


@allure.story("Routes config")
@allure.title("remove_workspace_route: removes by id, False when absent or file missing")
def test_remove_workspace_route(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    assert remove_workspace_route(root, "nope") is False
    (root / ".greedy-token.yaml").write_text(
        yaml.safe_dump({"routes": [{"id": "a", "target": "python"}]}),
        encoding="utf-8",
    )
    assert remove_workspace_route(root, "a") is True
    assert workspace_config_routes(root) == []


# ---------------------------------------------------------------- CLI handlers


@allure.story("CLI")
@allure.title("cmd_crystallize_draft: text + json output, lint status, error path")
def test_cmd_crystallize_draft(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None, capsys
) -> None:
    code = cli.cmd_crystallize_draft(_ns(crystal_id=CRYSTAL_ID))
    out = capsys.readouterr().out
    attach_text("stdout", out)
    assert code == 0
    assert "shadow until" in out
    assert "scripts lint OK" in out

    code = cli.cmd_crystallize_draft(_ns(crystal_id=CRYSTAL_ID, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["source"] == "template"
    assert payload["lint_ok"] is True

    code = cli.cmd_crystallize_draft(_ns(crystal_id="script-missing"))
    err = capsys.readouterr().err
    assert code == 1
    assert "crystallize draft:" in err


@allure.story("CLI")
@allure.title("cmd_crystallize_draft surfaces lint violations in text output")
def test_cmd_crystallize_draft_lint_failed(
    minimal_workspace: Path, crystal_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = l3.DraftResult(
        crystal_id=CRYSTAL_ID,
        pattern=TASK,
        hits=5,
        draft_path=minimal_workspace / ".greedy-token" / "drafts" / f"{CRYSTAL_ID}.py",
        config_path=minimal_workspace / ".greedy-token.yaml",
        shadow_until="2099-01-01T00:00:00Z",
        source="template",
        lint_ok=False,
        lint_violations=[{"id": CRYSTAL_ID, "kind": "script_missing", "detail": "boom"}],
    )
    monkeypatch.setattr(
        "greedy_token.crystallize_l3.draft_crystal",
        lambda cid, *, root, since, actor="", reason="": result,
    )
    code = cli.cmd_crystallize_draft(_ns(crystal_id=CRYSTAL_ID))
    out = capsys.readouterr().out
    attach_text("stdout", out)
    assert code == 1
    assert "Lint:    FAILED" in out
    assert "boom" in out
    code = cli.cmd_crystallize_draft(_ns(crystal_id=CRYSTAL_ID, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["lint_ok"] is False


@allure.story("CLI")
@allure.title("cmd_crystallize_promote: text, json, and error paths")
def test_cmd_crystallize_promote(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None, capsys
) -> None:
    code = cli.cmd_crystallize_promote(_ns(crystal_id=CRYSTAL_ID))
    err = capsys.readouterr().err
    assert code == 1
    assert "crystallize promote:" in err

    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    code = cli.cmd_crystallize_promote(_ns(crystal_id=CRYSTAL_ID))
    out = capsys.readouterr().out
    attach_text("stdout", out)
    assert code == 0
    assert "approved → applied" in out

    l3.reject_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    code = cli.cmd_crystallize_promote(_ns(crystal_id=CRYSTAL_ID, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True


@allure.story("CLI")
@allure.title("cmd_crystallize_reject: text and json outputs")
def test_cmd_crystallize_reject(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None, capsys
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    code = cli.cmd_crystallize_reject(_ns(crystal_id=CRYSTAL_ID))
    out = capsys.readouterr().out
    attach_text("stdout", out)
    assert code == 0
    assert "route removed=True" in out

    code = cli.cmd_crystallize_reject(_ns(crystal_id=CRYSTAL_ID, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["removed_route"] is False
