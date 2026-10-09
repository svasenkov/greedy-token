from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import allure
from greedy_token.router import TIER_ORDER, route_task, route_task_all_tiers
from tests.allure_reporting import attach_json, attach_text

pytestmark = [
    allure.epic("Routing"),
    allure.parent_suite("Routing"),
    allure.feature("Task router"),
    allure.suite("Task router"),
]


@allure.story("Invariants")
@allure.title("route_task never raises and always yields a valid tier for arbitrary input")
@given(
    task=st.text(max_size=120)
    | st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=120)
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_route_task_total_on_arbitrary_input(minimal_workspace: Path, task: str) -> None:
    # ollama pinned unavailable => deterministic, network-free routing.
    with patch("greedy_token.router.ollama_available", return_value=False):
        decision = route_task(task, minimal_workspace)
    assert decision.target in set(TIER_ORDER)
    assert decision.target
    assert decision.est_tokens >= 0
    assert isinstance(decision.route_id, str) and decision.route_id


@allure.story("Invariants")
@allure.title("route_task stays valid when the cheap LLM tier is available")
@given(task=st.text(max_size=120))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_route_task_total_with_ollama_available(minimal_workspace: Path, task: str) -> None:
    with patch("greedy_token.router.ollama_available", return_value=True):
        decision = route_task(task, minimal_workspace)
    assert decision.target in set(TIER_ORDER)
    assert decision.est_tokens >= 0


@allure.story("Scoring")
@allure.title("_score_patterns accumulates per-match weight and length bonus")
def test_score_patterns_accumulates() -> None:
    from greedy_token.router import _score_patterns

    with allure.step("Two matching patterns accumulate (score is not overwritten)"):
        score_both, matched = _score_patterns("alpha beta", ["alpha", "beta"])
        score_one, _ = _score_patterns("alpha beta", ["alpha"])
        assert matched == ["alpha", "beta"]
        assert score_both > score_one
    with allure.step("Exact weight: each match adds 1.0 + min(len/20, 2.0)"):
        expected = (1.0 + min(len("alpha") / 20.0, 2.0)) + (1.0 + min(len("beta") / 20.0, 2.0))
        assert score_both == expected
    with allure.step("Length bonus is capped at 2.0 for long patterns"):
        long_pat = "x" * 100
        score_long, _ = _score_patterns(long_pat, [long_pat])
        assert score_long == 1.0 + 2.0
    with allure.step("No match yields a zero score and empty match list"):
        assert _score_patterns("zzz", ["alpha"]) == (0.0, [])


@allure.story("Tool tier")
@allure.title("Route find task to tool tier with read-only plan")
def test_route_find_goes_to_tool(minimal_workspace: Path) -> None:
    with allure.step("Route find task"):
        decision = route_task("find baseUrl in sample.js", minimal_workspace)
        attach_json("decision", {"target": decision.target, "read_only": decision.read_only, "route_id": decision.route_id})
        attach_text("command", decision.command or "")
    with allure.step("Verify tool tier with read-only plan"):
        assert decision.target == "tool"
        assert decision.read_only is True
        assert decision.command is not None


@allure.story("RAG tier")
@allure.title("Route documentation question to RAG tier")
def test_route_rag_question(minimal_workspace: Path) -> None:
    with allure.step("Route documentation question"):
        decision = route_task("which -D flag for baseUrl", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify RAG tier selection"):
        assert decision.target == "rag"


@allure.story("Cursor tier")
@allure.title("Route open-ended task to cursor fallback")
def test_route_cursor_fallback(minimal_workspace: Path) -> None:
    with allure.step("Route open-ended explain task"):
        decision = route_task("explain quantum foam in repository layout", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify cursor fallback route"):
        assert decision.target == "cursor"
        assert decision.route_id == "cursor-fallback"


@patch("greedy_token.router.ollama_available", return_value=False)
@allure.story("Ollama availability")
@allure.title("Route skips Ollama tier when server is unavailable")
def test_route_skips_unavailable_ollama(mock_ollama, minimal_workspace: Path) -> None:
    with allure.step("Route audit task with Ollama unavailable"):
        decision = route_task("audit skill configurator-boolean", minimal_workspace)
        attach_json("decision", {"target": decision.target, "route_id": decision.route_id})
    with allure.step("Verify Ollama tier is skipped"):
        assert decision.target != "ollama"


@patch("greedy_token.router.ollama_available", return_value=True)
@allure.story("Ollama routes")
@allure.title("Draft-rag phrases no longer hit removed ollama-rag-draft stub")
def test_draft_rag_does_not_hit_removed_null_route(mock_ollama, minimal_workspace: Path) -> None:
    from greedy_token.executors import plan_run

    with allure.step("Route former phantom draft-rag phrase"):
        decision = route_task("draft rag chunk", minimal_workspace)
        plan = plan_run(decision, "draft rag chunk", minimal_workspace)
        attach_json(
            "decision",
            {
                "target": decision.target,
                "route_id": decision.route_id,
                "command": decision.command,
                "dry_run": plan.dry_run_output[:120],
            },
        )
    with allure.step("Verify no null-command ollama-rag-draft / No executor"):
        assert decision.route_id != "ollama-rag-draft"
        assert plan.dry_run_output != "No executor."
        if decision.target == "ollama":
            assert decision.command


@allure.story("Tier scan")
@allure.title("Full tier scan returns five executor rows")
def test_route_task_all_tiers_has_five_rows(minimal_workspace: Path) -> None:
    with allure.step("Run full tier scan for find task"):
        tiers = route_task_all_tiers("find baseUrl", minimal_workspace)
        attach_json("tier scan", [{"tier": t[0], "label": t[1]} for t in tiers])
    with allure.step("Verify five executor rows in order"):
        assert len(tiers) == 5
        assert [t[0] for t in tiers] == ["tool", "python", "ollama", "rag", "cursor"]


@allure.story("Explicit root")
@allure.title("Explicit root controls both route overlay and command cwd")
def test_explicit_root_controls_config_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit_root = tmp_path / "explicit"
    env_root = tmp_path / "from-env"
    explicit_root.mkdir()
    env_root.mkdir()
    route_template = (
        "routes:\n"
        "  - id: {route_id}\n"
        "    target: tool\n"
        "    tool: rg\n"
        "    read_only: true\n"
        "    patterns: [find]\n"
        "    search_paths: [.]\n"
    )
    (explicit_root / ".greedy-token.yaml").write_text(
        route_template.format(route_id="explicit-search"), encoding="utf-8"
    )
    (env_root / ".greedy-token.yaml").write_text(
        route_template.format(route_id="env-search"), encoding="utf-8"
    )
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(env_root))

    decision = route_task("find root marker", explicit_root)
    assert decision.route_id == "explicit-search"
    assert decision.command is not None
    assert json.dumps(str(explicit_root)) in decision.command
    assert json.dumps(str(env_root)) not in decision.command

    tier_rows = dict(route_task_all_tiers("find root marker", explicit_root))
    assert tier_rows["tool"].route_id == "explicit-search"
    assert json.dumps(str(explicit_root)) in (tier_rows["tool"].command or "")


@allure.story("Shadow routes")
@allure.title("Disabled shadow route does not execute script tier")
def test_disabled_shadow_route_is_skipped(minimal_workspace: Path) -> None:
    # Pin "now" inside the configured shadow window so the assertion does not
    # depend on the wall clock (the shadow_until date would otherwise expire).
    # Uses provider-balance (still shadow); access-diag was promoted live in v0.11.1.
    from datetime import datetime

    fixed_now = datetime(2026, 7, 1, tzinfo=UTC)
    with allure.step("Route task matching provider-balance shadow route"), patch(
        "greedy_token.router._now", return_value=fixed_now
    ):
        decision = route_task("provider balance", minimal_workspace)
        attach_json(
            "decision",
            {
                "target": decision.target,
                "route_id": decision.route_id,
                "shadow_route_id": decision.shadow_route_id,
            },
        )
    with allure.step("Verify disabled shadow route is skipped but logged"):
        assert decision.route_id != "python-provider-balance"
        assert decision.target == "cursor"
        assert decision.shadow_route_id == "python-provider-balance"


@allure.story("Token estimate")
@allure.title("Ollama available route reports non-zero est_tokens")
@patch("greedy_token.router.ollama_available", return_value=True)
def test_ollama_est_tokens_nonzero(mock_ollama, minimal_workspace: Path) -> None:
    from greedy_token.router import _token_estimate_for_route

    with allure.step("Estimate tokens for available ollama route"):
        complexity, est, rationale = _token_estimate_for_route(
            "ollama",
            task="audit skill configurator-boolean",
            root=minimal_workspace,
        )
        attach_json("estimate", {"complexity": complexity, "est_tokens": est, "rationale": rationale})
    with allure.step("Verify est_tokens is positive cheap-LLM spend"):
        assert est > 0
        assert "Cheap LLM" in rationale
        assert "0 API spend" not in rationale


@allure.story("Format decision")
@allure.title("format_decision includes command, domains, and cursor hint")
def test_format_decision_full(minimal_workspace: Path) -> None:
    from greedy_token.router import RouteDecision, format_decision

    rag_decision = RouteDecision(
        target="rag",
        route_id="rag-lookup",
        confidence=0.9,
        matched=["rag"],
        command=None,
        note="extra note",
        domains=["config"],
        complexity="low",
        est_tokens=100,
        rationale="lookup docs",
    )
    rag_out = format_decision(rag_decision, "baseUrl flag", minimal_workspace)
    assert "RAG domains" in rag_out
    assert "greedy-token rag" in rag_out

    tool_decision = RouteDecision(
        target="tool",
        route_id="tool-rg",
        confidence=0.9,
        matched=["find"],
        command="rg needle",
        note="",
        domains=[],
        complexity="low",
        est_tokens=0,
        rationale="search",
        read_only=True,
    )
    tool_out = format_decision(tool_decision, "find needle", minimal_workspace)
    assert "Command:" in tool_out
    assert "read-only" in tool_out

    cursor_out = format_decision(
        RouteDecision(
            target="cursor",
            route_id="cursor-fallback",
            confidence=0.3,
            matched=[],
            command=None,
            note="",
            domains=[],
            complexity="high",
            est_tokens=9000,
            rationale="wiring",
        ),
        "refactor header",
        minimal_workspace,
    )
    assert "New agent chat" in cursor_out


@allure.story("Explainable routing")
@allure.title("explain_route returns reason, matched, saved_est and runner_up")
def test_explain_route_structure(minimal_workspace: Path) -> None:
    from greedy_token.router import explain_route

    decision = route_task("find baseUrl in sample.js", minimal_workspace)
    exp = explain_route(decision, "find baseUrl in sample.js", minimal_workspace)
    attach_json("explanation", exp)
    assert exp["selected_tier"] == decision.target
    assert exp["route_id"] == decision.route_id
    assert exp["reason"]
    assert exp["matched"] == list(decision.matched)
    assert isinstance(exp["saved_est"], int)
    # runner_up is either a cheaper/alternative tier or the cursor fallback
    assert exp["runner_up"] is None or exp["runner_up"]["tier"] != decision.target


@allure.story("Explainable routing")
@allure.title("format_decision surfaces Why line for a matched route")
def test_format_decision_why_line(minimal_workspace: Path) -> None:
    from greedy_token.router import format_decision

    out = format_decision(
        route_task("find baseUrl in sample.js", minimal_workspace),
        "find baseUrl in sample.js",
        minimal_workspace,
    )
    assert "Why:" in out


@allure.story("Explainable routing")
@allure.title("explain_route on cursor fallback names the fallback reason")
def test_explain_route_cursor_fallback(minimal_workspace: Path) -> None:
    from greedy_token.router import explain_route

    task = "explain quantum foam in repository layout"
    decision = route_task(task, minimal_workspace)
    exp = explain_route(decision, task, minimal_workspace)
    assert decision.route_id == "cursor-fallback"
    assert "fallback" in exp["reason"].lower()


@allure.story("Explainable routing")
@allure.title("explain_route: rationale fallback, budget_policy note, saved-est error")
def test_explain_route_edge_branches(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.estimator as estimator
    from greedy_token.router import RouteDecision, explain_route

    # No matched patterns and not the cursor fallback → reason uses rationale;
    # a budget_policy note is appended; cursor_saved_for failure → saved_est 0.
    decision = RouteDecision(
        target="python",
        route_id="script-check-meta-sync",
        confidence=1.0,
        matched=[],
        command="python scripts/meta-sync-check.py",
        note="budget_policy: cheap tier forced by daily cap",
        domains=[],
        rationale="python tier chosen by policy",
    )

    def boom(*a, **k):
        raise ValueError("estimator down")

    monkeypatch.setattr(estimator, "cursor_saved_for", boom)
    exp = explain_route(decision, "some policy-driven task", minimal_workspace)
    attach_json("explanation", exp)
    assert exp["reason"].startswith("python tier chosen by policy")
    assert "budget_policy" in exp["reason"]
    assert exp["saved_est"] == 0


@allure.story("Explainable routing")
@allure.title("explain_route: empty rationale falls back to generic tier reason")
def test_explain_route_generic_reason(minimal_workspace: Path) -> None:
    from greedy_token.router import RouteDecision, explain_route

    decision = RouteDecision(
        target="rag",
        route_id="rag-lookup",
        confidence=0.5,
        matched=[],
        command=None,
        note="",
        domains=[],
        rationale="",
    )
    exp = explain_route(decision, "lookup something", minimal_workspace)
    assert exp["reason"] == "rag tier, no explicit pattern"


P1_POSITIVE_CASES = (
    ("archive-ru", "в lab/users.json у каждого объекта есть id и email", "lab/users.json", ("id", "email")),
    ("ru-comma", "в lab/users.json у каждого объекта есть id, email", "lab/users.json", ("id", "email")),
    ("ru-check", "проверь, что в lab/users.json у каждого объекта есть id и email", "lab/users.json", ("id", "email")),
    ("ru-object-path", "проверь, что у каждого объекта в lab/users.json есть id и email", "lab/users.json", ("id", "email")),
    ("ru-single-quotes", "в 'lab/users.json' у каждого объекта есть 'id' и 'email'", "lab/users.json", ("id", "email")),
    ("ru-double-quotes", 'в "lab/users.json" у каждого объекта есть "id" и "email"', "lab/users.json", ("id", "email")),
    ("ru-three-fields", "в lab/users.json у каждого объекта есть id, email и role", "lab/users.json", ("id", "email", "role")),
    ("en-statement", "each object in lab/users.json has id and email", "lab/users.json", ("id", "email")),
    ("en-check", "check each object in lab/users.json has keys id and email", "lab/users.json", ("id", "email")),
    ("en-verify", "verify that each object in lab/users.json has keys id, email", "lab/users.json", ("id", "email")),
    ("en-three-fields", "check each object in lab/users.json has id, email and role", "lab/users.json", ("id", "email", "role")),
    ("en-quoted-fields", 'check each object in lab/users.json has keys "id", "email"', "lab/users.json", ("id", "email")),
    ("quoted-space-path", 'в "lab/User Data.json" у каждого объекта есть id и email', "lab/User Data.json", ("id", "email")),
    ("slot-case", "в lab/Users.json у каждого объекта есть ID и eMail", "lab/Users.json", ("ID", "eMail")),
    ("named-params", "check json keys path=lab/users.json keys=id,email", "lab/users.json", ("id", "email")),
    ("quoted-param-path", 'check json keys path="lab/User Data.json" keys=id,email', "lab/User Data.json", ("id", "email")),
)

P1_ADVERSARIAL_CASES = (
    ("negation-ru", "не проверяй: в lab/users.json у каждого объекта есть id и email"),
    ("negation-en", "do not check each object in lab/users.json has id and email"),
    ("negated-keys-ru", "в lab/users.json у каждого объекта нет id и email"),
    ("negated-keys-en", "each object in lab/users.json has no id and email"),
    ("never", "never check each object in lab/users.json has id and email"),
    ("without", "check each object in lab/users.json has id without email"),
    ("compound-edit-ru", "в lab/users.json у каждого объекта есть id и email и измени id"),
    ("compound-delete-ru", "проверь, что в lab/users.json у каждого объекта есть id и email и удали объект"),
    ("compound-send-ru", "в lab/users.json у каждого объекта есть id и email и отправь отчёт"),
    ("compound-edit-en", "check each object in lab/users.json has id and email and modify id"),
    ("compound-delete-en", "check each object in lab/users.json has id and email then delete it"),
    ("compound-send-en", "check each object in lab/users.json has id and email and send a report"),
    ("action-as-key-delete", "each object in lab/users.json has id and delete"),
    ("action-as-key-send", "each object in lab/users.json has id and send"),
    ("action-as-key-not", "each object in lab/users.json has id and not"),
    ("edit-prefix", "fix each object in lab/users.json has id and email"),
    ("hypothetical", "if each object in lab/users.json has id and email"),
    ("explanation", "explain each object in lab/users.json has id and email"),
    ("quotation-as-request", '"в lab/users.json у каждого объекта есть id и email"'),
    ("documentation", "в lab/users.json у каждого объекта есть id и email as documentation"),
    ("unknown-flag", "check json keys path=lab/users.json keys=id,email --force"),
    ("unknown-param", "check json keys path=lab/users.json keys=id,email mode=strict"),
    ("duplicate-param", "check json keys path=lab/users.json keys=id,email path=lab/events.json"),
    ("extra-token", "check json keys path=lab/users.json keys=id,email surprise"),
    ("two-paths-or", "в lab/users.json или lab/events.json у каждого объекта есть id и email"),
    ("two-paths-and", "в lab/users.json и lab/events.json у каждого объекта есть id и email"),
    ("two-paths-comma", "в lab/users.json, lab/events.json у каждого объекта есть id и email"),
    ("omitted-path", "проверь, что у каждого объекта есть id и email"),
    ("missing-keys", "в lab/users.json у каждого объекта есть"),
    ("missing-path-param", "check json keys keys=id,email"),
    ("missing-keys-param", "check json keys path=lab/users.json"),
    ("duplicate-keys", "в lab/users.json у каждого объекта есть id и id"),
    ("alternative-keys-ru", "в lab/users.json у каждого объекта есть id или email"),
    ("alternative-keys-en", "each object in lab/users.json has id or email"),
    ("empty-list-entry", "each object in lab/users.json has id,,email"),
    ("trailing-list-comma", "each object in lab/users.json has id,email,"),
    ("repeated-conjunction", "each object in lab/users.json has id and email and role"),
    ("ambiguous-list", "each object in lab/users.json has id email"),
    ("unterminated-path-quote", 'в "lab/users.json у каждого объекта есть id и email'),
    ("unterminated-key-quote", 'в lab/users.json у каждого объекта есть id и "email'),
    ("key-with-space", 'each object in lab/users.json has id and "email address"'),
    ("smart-quotes", "в «lab/users.json» у каждого объекта есть id и email"),
    ("backticks", "в `lab/users.json` у каждого объекта есть id и email"),
    ("semicolon", "в lab/users.json у каждого объекта есть id и email; delete it"),
    ("newline", "в lab/users.json у каждого объекта есть id и email\ncheck again"),
    ("nul", "в lab/users.json у каждого объекта есть id и email\x00"),
    ("substitution", "check json keys path=$(id).json keys=id,email"),
    ("environment", "check json keys path=$HOME/users.json keys=id,email"),
    ("pipe", "check json keys path=lab/users.json keys=id,email | send"),
    ("redirect", "check json keys path=lab/users.json keys=id,email > report.json"),
    ("parent-escape", "в ../users.json у каждого объекта есть id и email"),
    ("embedded-parent", "в lab/../users.json у каждого объекта есть id и email"),
    ("absolute-path", "в /tmp/users.json у каждого объекта есть id и email"),
    ("home-path", "в ~/users.json у каждого объекта есть id и email"),
    ("windows-path", "в C:/users.json у каждого объекта есть id и email"),
    ("drive-relative", "в C:users.json у каждого объекта есть id и email"),
    ("unc-path", "в //server/users.json у каждого объекта есть id и email"),
    ("line-reference", "в lab/users.json:7 у каждого объекта есть id и email"),
    ("path-option", "check json keys path=--users.json keys=id,email"),
    ("key-option", "check json keys path=lab/users.json keys=id,--email"),
    ("missing-file", "в lab/missing.json у каждого объекта есть id и email"),
    ("null-semantics", "в lab/users.json у каждого объекта есть id и email not null"),
    ("value-semantics", "в lab/users.json у каждого объекта есть id и email=email@example.test"),
    ("second-check", "в lab/users.json у каждого объекта есть id и email и проверь role"),
)


@pytest.fixture
def p1_workspace(tmp_path, monkeypatch):
    import greedy_token.budget_policy as budget_policy
    import greedy_token.router as router

    lab = tmp_path / "lab"
    lab.mkdir(exist_ok=True)
    for name in ("users.json", "events.json", "User Data.json", "Users.json"):
        (lab / name).write_text("[]", encoding="utf-8")
    monkeypatch.setattr(router, "ollama_available", lambda: False)
    monkeypatch.setattr(router, "_metered_bulk_ready", lambda root: False)
    monkeypatch.setattr(router, "_token_estimate_for_route", lambda target, **kw: ("low", 0, "mock"))
    monkeypatch.setattr(budget_policy, "apply_budget_policy", lambda decision, *a: decision)
    return tmp_path


def p1_json_route(path="lab/users.json", keys=("id", "email")):
    return {
        "id": "python-json-keys", "target": "python", "read_only": True,
        "command": f"python lab/check_users.py --path {json.dumps(path)} --keys {','.join(keys)}",
        "patterns": ["каждого объекта", "each object", "check json keys",
                     f"в {json.dumps(path)} у каждого объекта есть {', '.join(keys)}"],
    }


@pytest.mark.parametrize("case_id,prompt,path,keys", P1_POSITIVE_CASES, ids=[c[0] for c in P1_POSITIVE_CASES])
def test_p1_json_positive_exact_slots(p1_workspace, monkeypatch, case_id, prompt, path, keys):
    import greedy_token.router as router

    slots = router.parse_json_keys_intent(prompt, p1_workspace)
    assert slots is not None
    assert (slots.intent, slots.path, slots.keys) == ("json_keys", path, keys)
    route = p1_json_route(path, keys)
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    decision = router.route_task(prompt, p1_workspace)
    assert (decision.target, decision.route_id, decision.read_only) == ("python", route["id"], True)
    assert decision.intent_slots == slots
    assert router.first_matching_route_id(prompt, p1_workspace) == route["id"]
    assert dict(router.route_task_all_tiers(prompt, p1_workspace))["python"].intent_slots == slots


@pytest.mark.parametrize("case_id,prompt", P1_ADVERSARIAL_CASES, ids=[c[0] for c in P1_ADVERSARIAL_CASES])
def test_p1_adversarial_no_false_cheap(p1_workspace, monkeypatch, case_id, prompt):
    import greedy_token.router as router

    route = p1_json_route()
    route["patterns"] = ["каждого объекта", "each object", "check json keys"]
    if case_id == "omitted-path":
        route["command"] = "python lab/check_users.py"
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    assert router.parse_json_keys_intent(prompt, p1_workspace) is None
    assert router.route_task(prompt, p1_workspace).target == "cursor"
    assert router.first_matching_route_id(prompt, p1_workspace) is None
    assert all(not d.matched for tier, d in router.route_task_all_tiers(prompt, p1_workspace) if tier != "cursor")


def test_p1_fixed_slots_are_bound_to_one_declared_context(p1_workspace, monkeypatch):
    import greedy_token.router as router

    prompt = "проверь, что у каждого объекта есть id и email"
    assert router.parse_json_keys_intent(prompt, p1_workspace) is None
    route = p1_json_route()
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    slots = router.route_task(prompt, p1_workspace).intent_slots
    assert (slots.intent, slots.path, slots.keys) == ("json_keys", "lab/users.json", ("id", "email"))
    for unsupported in (
        "в lab/events.json у каждого объекта есть id и email",
        "в lab/users.json у каждого объекта есть id и role",
    ):
        assert router.route_task(unsupported, p1_workspace).target == "cursor"
    route["patterns"].append("в lab/events.json у каждого объекта есть id и email")
    assert router.route_task(prompt, p1_workspace).target == "cursor"
    route["patterns"] = ["каждого объекта", "id и email"]
    route["command"] = "python lab/check_users.py"
    assert router.route_task(prompt, p1_workspace).target == "cursor"


def test_p1_symlink_escape_is_not_a_path_slot(p1_workspace):
    import greedy_token.router as router

    outside = p1_workspace.parent / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    (p1_workspace / "lab" / "escape.json").symlink_to(outside)
    assert router.parse_json_keys_intent("в lab/escape.json у каждого объекта есть id и email", p1_workspace) is None


@pytest.mark.parametrize("command", [
    "python lab/check_users.py --path lab/events.json --keys id,email",
    "python lab/check_users.py --path lab/users.json --keys id,role",
    "python lab/check_users.py",
    "python lab/check_users.py --path lab/users.json --keys id,email --unknown",
    "python ../check.py --path lab/users.json --keys id,email",
    "python lab/../check.py --path lab/users.json --keys id,email",
])
def test_p1_fixed_command_cannot_contradict_slots(p1_workspace, monkeypatch, command):
    import greedy_token.router as router

    route = p1_json_route()
    route["command"] = command
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    prompt = "в lab/users.json у каждого объекта есть id и email"
    assert router.route_task(prompt, p1_workspace).target == "cursor"
    assert router.first_matching_route_id(prompt, p1_workspace) is None


@pytest.mark.parametrize("prompt", [
    "each object in lab/users.json has id and ſend",
    'each object in lab/users.json has id and "and"',
    "each object in lab/users.json has id, no",
    'each object in " lab/users.json" has id and email',
    'each object in "lab/users.json " has id and email',
])
def test_p1_reserved_and_non_ascii_keys_fail_closed(p1_workspace, prompt):
    import greedy_token.router as router

    assert router.parse_json_keys_intent(prompt, p1_workspace) is None


def test_p1_unavailable_alias_does_not_resolve_ambiguous_default(p1_workspace, monkeypatch):
    import greedy_token.router as router

    route = p1_json_route()
    route["patterns"].append("в lab/missing.json у каждого объекта есть id и email")
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    assert router.route_task("проверь, что у каждого объекта есть id и email", p1_workspace).target == "cursor"


@pytest.mark.parametrize("prompt", ["show last 11 commits and send report", "do not show last 11 commits", "show last 11 commits --force"])
def test_p1_exact_alias_does_not_override_args_refusal(p1_workspace, monkeypatch, prompt):
    import greedy_token.router as router

    route = {"id": "python-git-recent", "target": "python", "read_only": True,
             "command": "python scripts/git-recent.py", "params": ["args"],
             "patterns": [prompt], "args_from_prompt": [{"regex": r"last ([0-9]+) commits", "args": "--count {0}"}]}
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    assert router.route_task(prompt, p1_workspace).target == "cursor"
    assert router.first_matching_route_id(prompt, p1_workspace) is None


@pytest.mark.parametrize("prompt,count,args", [
    ("last commits", None, ""),
    ("recent commits", None, ""),
    ("what changed in last commits", None, ""),
    ("show last commits", None, "--compact"),
    ("what changed in recent commits", None, ""),
    ("show last 3 commits", 3, "--count 3 --compact"),
])
def test_p1_recent_commits_alias_route_default_argv(p1_workspace, minimal_workspace, prompt, count, args):
    import greedy_token.router as router
    from greedy_token.hook_policy import derive_prompt_args
    from greedy_token.subprocess_safe import command_to_argv

    root = minimal_workspace
    route = next(r for r in router.load_routes_config(root)["routes"] if r["id"] == "python-git-recent")
    cwd, argv = command_to_argv(route["command"], default_cwd=root, workspace_root=root)
    slots = router.parse_recent_commits_intent(prompt)
    decision = router.route_task(prompt, root)
    covered_by = router.first_matching_route_id(prompt, root)
    derived = derive_prompt_args(prompt, route["args_from_prompt"], root=root)
    print(json.dumps({"alias": prompt, "slots": str(slots), "route": decision.route_id,
                      "argv": decision.command_argv, "cwd": str(decision.command_cwd),
                      "default_argv": argv, "derived_args": derived, "covered_by": covered_by}, sort_keys=True))
    assert (decision.target, decision.route_id, decision.read_only) == ("python", route["id"], True)
    assert decision.intent_slots == router.IntentSlots("recent_commits", count=count)
    assert decision.command == route["command"]
    assert decision.command_argv == tuple(argv)
    assert decision.command_cwd == cwd == root.resolve()
    assert derived == args
    assert covered_by == route["id"]


@pytest.mark.parametrize("prompt", [
    template.format(alias)
    for alias in ("last commits", "recent commits", "what changed in last commits")
    for template in (
        '"{}"', "'{}'", "`{}`", "do not {}", "never {}", "if {}", "suppose {}",
        "{} and send report", "{} then show status", "{} and fix the bug",
        "{} --force", "{} --count 3", "{}; git status", "{} | cat", "{} $(id)",
        "{} > report.json", "{} path=../outside", "{} in ../outside", "{}\n", "{}\x00",
    )
] + [
    "commits", "latest commits", "last 3 commits", "recent 3 commits",
    "what changed in latest commits", "what changed in last 3 commits",
    "last commit", "recent commit", "last commits please",
    "show last 0 commits", "show last 10000 commits", "show last 99999 commits",
    "show last -1 commits", "show last 1.5 commits", "show last three commits",
    "show last 3commits", "show last 3 commits --root ../outside",
])
def test_p1_recent_commits_alias_rejects_unsafe_prompt(p1_workspace, minimal_workspace, prompt):
    import greedy_token.router as router

    decision = router.route_task(prompt, minimal_workspace)
    assert router.parse_recent_commits_intent(prompt) is None
    assert decision.target == "cursor"
    assert (decision.command, decision.command_argv, decision.command_cwd, decision.intent_slots) == (None, None, None, None)
    assert router.first_matching_route_id(prompt, minimal_workspace) is None


@pytest.mark.parametrize("alias", ["last commits", "recent commits", "what changed in last commits"])
@pytest.mark.parametrize("scope,target", [
    ("of repo Other", "python"), ("in repository projects/Other", "python"),
    ("for repo Other", "python"), ("of repo ../outside", "cursor"),
    ("of repo /tmp/outside", "cursor"), ("of repo C:/outside", "cursor"),
])
def test_p1_recent_commits_alias_repository_scope_no_invocation(p1_workspace, minimal_workspace, alias, scope, target):
    import greedy_token.router as router

    prompt = f"{alias} {scope}"
    decision = router.route_task(prompt, minimal_workspace)
    assert decision.target == target
    assert (decision.command, decision.command_argv, decision.command_cwd, decision.intent_slots) == (None, None, None, None)
    assert router.first_matching_route_id(prompt, minimal_workspace) is None
    if target == "python":
        assert "advisory only" in decision.note


P1_COUNT_POSITIVE_CASES = (
    ("count-ru", "покажи последних 11 коммитов", 11),
    ("count-ru-list", "список последних 5 коммитов", 5),
    ("count-en", "show last 11 commits", 11),
    ("count-en-list", "list latest 5 commits", 5),
)
P1_COUNT_NEGATIVE_CASES = (
    "покажи последних 11 коммитов с неизвестным параметром",
    "покажи последних 11 коммитов --force",
    "show last 11 commits and send report",
    "покажи последних 11 коммитов path=../outside",
    "покажи последних 0 коммитов",
    "покажи последних 10000 коммитов",
    "покажи последних 11 коммитов $(id)",
    "не покажи последних 11 коммитов",
)
P1_COUNT_SPEC = [
    {"regex": r"([0-9]{1,4}) +коммит", "args": "--count {0}"},
    {"regex": r"(?:last|latest) +([0-9]{1,4}) +commits?", "args": "--count {0}"},
]


@pytest.mark.parametrize("case_id,prompt,count", P1_COUNT_POSITIVE_CASES, ids=[c[0] for c in P1_COUNT_POSITIVE_CASES])
def test_p1_count_route_exact_slot(p1_workspace, monkeypatch, case_id, prompt, count):
    import greedy_token.router as router

    route = {"id": "python-git-recent", "target": "python", "read_only": True,
             "command": "python scripts/git-recent.py", "params": ["args"],
             "patterns": ["коммит", "commits"], "args_from_prompt": P1_COUNT_SPEC}
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    decision = router.route_task(prompt, p1_workspace)
    assert decision.target == "python"
    assert (decision.intent_slots.intent, decision.intent_slots.path, decision.intent_slots.keys, decision.intent_slots.count) == ("recent_commits", "", (), count)


@pytest.mark.parametrize("prompt", P1_COUNT_NEGATIVE_CASES)
def test_p1_count_route_rejects_partial_parameter_match(p1_workspace, monkeypatch, prompt):
    import greedy_token.router as router

    route = {"id": "python-git-recent", "target": "python", "read_only": True,
             "command": "python scripts/git-recent.py", "params": ["args"],
             "patterns": ["коммит", "commits"], "args_from_prompt": P1_COUNT_SPEC}
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    assert router.route_task(prompt, p1_workspace).target == "cursor"
    assert router.first_matching_route_id(prompt, p1_workspace) is None


@pytest.mark.parametrize("prompt", ["find commits", 'find "each object"', 'find "json keys"'])
def test_p1_unrelated_literal_search_keeps_tool_grammar(p1_workspace, monkeypatch, prompt):
    import greedy_token.router as router

    count_route = {"id": "python-git-recent", "target": "python", "read_only": True,
                   "command": "python scripts/git-recent.py", "params": ["args"],
                   "patterns": ["commits"], "args_from_prompt": P1_COUNT_SPEC}
    tool_route = {"id": "tool-rg-search", "target": "tool", "read_only": True, "patterns": ["find"]}
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [p1_json_route(), count_route, tool_route]})
    assert router.route_task(prompt, p1_workspace).target == "tool"
    assert router.first_matching_route_id(prompt, p1_workspace) == "tool-rg-search"


def test_p1_existing_routing_corpus_controlled(p1_workspace, monkeypatch):
    import greedy_token.router as router
    from tests import test_routing_corpus as corpus

    monkeypatch.setattr(router, "ollama_available", lambda: True)
    cases = corpus._load_corpus()["cases"]
    mismatches = [{"id": case["id"], "task": case["task"], "expected": case["expected_target"],
                   "actual": router.route_task(case["task"], p1_workspace).target}
                  for case in cases if router.route_task(case["task"], p1_workspace).target != case["expected_target"]]
    adversarial = [case for case in cases if case["family"] == corpus.FALSE_CHEAP_FAMILY]
    false_cheap = [case["id"] for case in adversarial if router.route_task(case["task"], p1_workspace).target != "cursor"]
    print(json.dumps({"existing_routing_corpus": len(cases), "adversarial": len(adversarial),
                      "false_cheap": false_cheap, "routing_mismatches": mismatches}, ensure_ascii=False, sort_keys=True))
    assert not false_cheap
    corpus.test_routing_corpus_quality_gate(p1_workspace)


def test_p1_corpus_identity():
    import hashlib

    corpus = {"version": "p1-slots-v1", "positive": P1_POSITIVE_CASES, "adversarial": P1_ADVERSARIAL_CASES}
    ids = [row[0] for row in (*P1_POSITIVE_CASES, *P1_ADVERSARIAL_CASES)]
    assert len(ids) == len(set(ids))
    encoded = json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    print(json.dumps({"corpus": "p1-slots-v1", "positive": len(P1_POSITIVE_CASES),
                      "adversarial": len(P1_ADVERSARIAL_CASES), "sha256": hashlib.sha256(encoded).hexdigest()}, sort_keys=True))


P1_REPOSITORY_ADVISORY_CASES = (
    "what changed in last commits of repo X",
    "git log of repo X",
    "what changed in recent commits of repository Alpha",
    "git log in repository Beta",
    "show last 7 commits for repo Product-Api",
    "list latest 5 commits of repo projects/CaseSensitive",
    "show recent commits in repo Delta",
    'git log of repo "Product API"',
    "what changed of repository Z",
    "git log for repo projects/Api",
)
P1_REPOSITORY_REFUSAL_CASES = (
    "do not git log of repo X",
    "git log of repo X and fix the bug",
    "what changed in last commits of repo X and send a report",
    "git log of repo X; send a report",
    "git log of repo X --force",
    "show last 0 commits of repo X",
    "show last 10000 commits of repo X",
    "git log of repo ../outside",
    "git log of repo projects/../outside",
    "git log of repo /tmp/outside",
    "git log of repo ~/outside",
    "git log of repo C:/outside",
    "git log of repo",
    "git log of repo X or Y",
    "git log of repo X repo=Y",
    'git log of repo "X; send"',
    "git log --since yesterday of repo X",
    "git log of repo X please",
    "git log of repo $(id)",
    "git log of repo send",
)


def p1_commits_route():
    return {
        "id": "python-p1-history", "target": "python", "read_only": True,
        "command": "python scripts/p1-history.py", "params": ["args"],
        "patterns": ["what changed", "commits", "git log"],
        "args_from_prompt": P1_COUNT_SPEC,
    }


@pytest.mark.parametrize("prompt", P1_REPOSITORY_ADVISORY_CASES)
def test_p1_repository_recommendation_has_no_execution_binding(p1_workspace, monkeypatch, prompt):
    import greedy_token.router as router

    route = p1_commits_route()
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    decision = router.route_task(prompt, p1_workspace)
    assert (decision.target, decision.route_id) == ("python", route["id"])
    assert decision.intent_slots is None
    assert (decision.command, decision.command_argv, decision.command_cwd) == (None, None, None)
    assert "advisory only" in decision.note
    assert router.first_matching_route_id(prompt, p1_workspace) is None
    scan = dict(router.route_task_all_tiers(prompt, p1_workspace))["python"]
    assert scan.route_id == route["id"]
    assert (scan.command, scan.command_argv, scan.command_cwd, scan.intent_slots) == (None, None, None, None)


@pytest.mark.parametrize("missing_structured_argv", [False, True])
@pytest.mark.parametrize("prompt", P1_REPOSITORY_ADVISORY_CASES)
def test_p1_repository_plan_cannot_rebuild_default_workspace_argv(
    p1_workspace, monkeypatch, prompt, missing_structured_argv,
):
    from dataclasses import replace
    from unittest.mock import Mock

    import greedy_token.executors as executors
    import greedy_token.router as router
    from greedy_token.subprocess_safe import CommandInvocation

    decision = router._decision_from_route(
        p1_commits_route(), score=10.0, matched=["git log", "commits"],
        task=prompt, root=p1_workspace,
    )
    if missing_structured_argv:
        decision = replace(decision, command_argv=None, command_cwd=None)
    parser = Mock(wraps=executors.command_to_argv)
    derived = Mock(return_value=())
    manifest = Mock(return_value=frozenset({"scripts/p1-history.py"}))
    authorize = Mock(return_value=CommandInvocation(
        cwd=p1_workspace, argv=("python", "scripts/p1-history.py"),
        authorization="test-only-ready",
    ))
    monkeypatch.setattr(executors, "command_to_argv", parser)
    monkeypatch.setattr(executors, "_prompt_derived_args", derived)
    monkeypatch.setattr(executors, "trusted_manifest_paths", manifest)
    monkeypatch.setattr(executors, "trusted_script_argv", authorize)
    plan = executors.plan_run(decision, prompt, p1_workspace)
    assert plan.executable is False
    assert (plan.command, plan.argv, plan.cwd, plan.authorization) == (None, None, None, "")
    for operation in (parser, derived, manifest, authorize):
        operation.assert_not_called()


@pytest.mark.parametrize("prompt", P1_REPOSITORY_REFUSAL_CASES)
def test_p1_repository_invalid_intent_is_not_recommended(p1_workspace, monkeypatch, prompt):
    import greedy_token.router as router

    route = p1_commits_route()
    route["patterns"].append(prompt)
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    assert router.route_task(prompt, p1_workspace).target == "cursor"
    assert router.first_matching_route_id(prompt, p1_workspace) is None
    assert all(not decision.matched for tier, decision in router.route_task_all_tiers(prompt, p1_workspace) if tier != "cursor")


@pytest.mark.parametrize("scope", ["X", "projects/CaseSensitive"])
def test_p1_existing_repo_directory_is_not_trusted_scope_binding(p1_workspace, monkeypatch, scope):
    import greedy_token.router as router

    (p1_workspace / scope).mkdir(parents=True)
    route = p1_commits_route()
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    decision = router.route_task(f"git log of repo {scope}", p1_workspace)
    assert decision.target == "python"
    assert (decision.command, decision.command_argv, decision.command_cwd, decision.intent_slots) == (None, None, None, None)


def test_p1_repository_symlink_escape_remains_refused(p1_workspace, monkeypatch):
    import greedy_token.router as router

    outside = p1_workspace.parent / "p1-outside-repo"
    outside.mkdir()
    (p1_workspace / "escape").symlink_to(outside, target_is_directory=True)
    route = p1_commits_route()
    monkeypatch.setattr(router, "load_routes_config", lambda root: {"routes": [route]})
    assert router.route_task("git log of repo escape", p1_workspace).target == "cursor"


def test_p1_repository_separation_corpus_identity():
    import hashlib

    corpus = {"version": "p1-repository-admission-v1", "advisory": P1_REPOSITORY_ADVISORY_CASES,
              "refused": P1_REPOSITORY_REFUSAL_CASES}
    encoded = json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert len(set((*P1_REPOSITORY_ADVISORY_CASES, *P1_REPOSITORY_REFUSAL_CASES))) == 30
    print(json.dumps({"corpus": corpus["version"], "advisory": len(P1_REPOSITORY_ADVISORY_CASES),
                      "refused": len(P1_REPOSITORY_REFUSAL_CASES), "sha256": hashlib.sha256(encoded).hexdigest()}, sort_keys=True))


P1_REWORK_CONFLICTS = ("command-path", "command-keys", "command-cwd", "command-script", "missing-command")
P1_REWORK_WHITESPACE = (
    "each  object in lab/users.json has id and email",
    "each  object in lab/users.json has id,email",
    "each\tobject in lab/users.json has id,email",
    "check  json keys path=lab/users.json keys=id,email",
    "check json  keys path=lab/users.json keys=id,email",
    "в lab/users.json у каждого  объекта есть id,email",
    "в lab/users.json у каждого\tобъекта есть id,email",
    "each object  in lab/users.json has id,email",
)
P1_REWORK_DERIVATIONS = ("show ../secrets", "show lab/../secrets", "show escape/secrets", "show unknown")
P1_REWORK_QUOTED = (
    ('each object in "lab/of repo Archive/users.json" has id and email', "lab/of repo Archive/users.json", ("id", "email")),
    ('check json keys path="lab/for repository Data.json" keys=id,email', "lab/for repository Data.json", ("id", "email")),
    ("each object in 'lab/in repo Case/users.json' has ID and eMail", "lab/in repo Case/users.json", ("ID", "eMail")),
    ('check json keys path="lab/for repository  Data.json" keys=ID,eMail', "lab/for repository  Data.json", ("ID", "eMail")),
)
P1_REWORK_CONTROLS = (
    ("archive", P1_POSITIVE_CASES[0][1], "execute"),
    ("fixed", "check json keys path=lab/users.json keys=id,email", "execute"),
    ("derived", 'check json keys path="lab/User Data.json" keys=ID,eMail', "execute"),
    ("no-derivation", "git log", "execute"),
    ("repo-double-space", "git log  of repo X", "advisory"),
    ("repo-double-space-count", "show last 7 commits  for repo Product-Api", "advisory"),
    ("literal-search", 'find "each  object"', "tool"),
)


@pytest.fixture
def p1_rework_driver(p1_workspace, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import greedy_token.capabilities as capabilities
    import greedy_token.capabilities_invoke as invoke
    import greedy_token.executors as executors
    import greedy_token.paths as paths
    import greedy_token.router as router
    from greedy_token.trust import approve_script

    root = p1_workspace
    for path in ("lab/check_users.py", "lab/other.py", "scripts/p1-history.py"):
        script = root / path
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("print('{\"ok\": true}')\n", encoding="utf-8")
        approve_script(root, path)
    for _, path, _ in P1_REWORK_QUOTED:
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("[]", encoding="utf-8")
    outside = root.parent / f"{root.name}-outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    execute = Mock(return_value=executors.PlanRunResult(0, '{"ok": true}', True, "produced"))
    planner = Mock(wraps=executors.plan_run)
    monkeypatch.setattr(invoke, "execute_plan", execute)
    monkeypatch.setattr(invoke, "plan_run", planner)

    def install(route):
        config = {"routes": [route]}
        monkeypatch.setattr(router, "load_routes_config", lambda *a, **kw: config)
        monkeypatch.setattr(paths, "load_routes_config", lambda *a, **kw: config)
        monkeypatch.setattr(invoke, "load_routes_config", lambda *a, **kw: config)
        cap = capabilities.Capability(
            id=route["id"], source="route", origin="workspace", tier=route["target"],
            read_only=True, status="active", readiness="ready", reason="", invocable=True,
            command=route.get("command", ""), argv=tuple(route.get("argv", ())),
            params=tuple(route.get("params", ())), patterns=tuple(route["patterns"]),
        )
        monkeypatch.setattr(capabilities, "capability_by_id", lambda *a: cap)
        monkeypatch.setattr(invoke, "capability_by_id", lambda *a: cap)
        return cap

    return SimpleNamespace(root=root, install=install, execute=execute, planner=planner)


def p1_rework_conflicting_route(conflict, root):
    route = p1_json_route()
    route["argv"] = ["python", "lab/check_users.py", "--path", "lab/users.json", "--keys", "id,email"]
    if conflict == "command-path":
        route["command"] = p1_json_route("lab/events.json")["command"]
    elif conflict == "command-keys":
        route["command"] = p1_json_route(keys=("id", "role"))["command"]
    elif conflict == "command-cwd":
        route["command"] = f"cd {json.dumps(str(root / 'lab'))} && {route['command']}"
    elif conflict == "command-script":
        route["command"] = route["command"].replace("lab/check_users.py", "lab/other.py")
    else:
        route.pop("command")
    return route


def p1_rework_derivation_route(prompt):
    return {
        "id": "python-rework-derive", "target": "python", "read_only": True,
        "command": "python lab/check_users.py", "params": ["args"],
        "patterns": [prompt], "args_from_prompt": [{"regex": r"show (.*)", "args": "{0}"}],
    }


@pytest.mark.parametrize("conflict", P1_REWORK_CONFLICTS)
def test_p1_rework_conflicting_fixed_binding_plan(p1_rework_driver, conflict):
    import greedy_token.capabilities_invoke as invoke
    import greedy_token.executors as executors
    import greedy_token.router as router

    driver = p1_rework_driver
    route = p1_rework_conflicting_route(conflict, driver.root)
    driver.install(route)
    prompt = "check json keys path=lab/users.json keys=id,email"
    decision = router._decision_from_route(route, score=10, matched=[prompt], task=prompt, root=driver.root)
    plan = executors.plan_run(decision, prompt, driver.root)
    result = invoke.invoke_capability(driver.root, route["id"], log=False)
    print(json.dumps({"finding": "F1", "case": conflict, "slots": str(decision.intent_slots),
                      "argv": plan.argv, "cwd": str(plan.cwd), "executable": plan.executable,
                      "invoke_executed": result.executed}, sort_keys=True))
    assert not plan.executable
    assert (plan.command, plan.argv, plan.cwd) == (None, None, None)
    assert not result.executed
    driver.execute.assert_not_called()
    assert router.first_matching_route_id(prompt, driver.root) is None


@pytest.mark.parametrize("prompt", P1_REWORK_WHITESPACE)
def test_p1_rework_malformed_whitespace_plan(p1_rework_driver, prompt):
    import greedy_token.executors as executors
    import greedy_token.router as router

    driver = p1_rework_driver
    route = p1_json_route()
    route["patterns"].append(prompt)
    driver.install(route)
    decision = router._decision_from_route(route, score=10, matched=[prompt], task=prompt, root=driver.root)
    plan = executors.plan_run(decision, prompt, driver.root)
    assert not plan.executable, (decision.intent_slots, plan.argv, plan.cwd)
    assert (plan.command, plan.argv, plan.cwd) == (None, None, None)
    assert router.route_task(prompt, driver.root).target == "cursor"
    assert router.first_matching_route_id(prompt, driver.root) is None


@pytest.mark.parametrize("prompt", P1_REWORK_DERIVATIONS)
def test_p1_rework_rejected_derivation_legacy_plan(p1_rework_driver, prompt):
    import greedy_token.executors as executors
    import greedy_token.router as router

    driver = p1_rework_driver
    route = p1_rework_derivation_route(prompt)
    driver.install(route)
    decision = router.RouteDecision(
        target="python", route_id=route["id"], confidence=1.0, matched=[prompt],
        command=route["command"], note="", domains=[], read_only=True,
        command_argv=("python", "lab/check_users.py"), command_cwd=driver.root,
    )
    plan = executors.plan_run(decision, prompt, driver.root)
    assert not plan.executable, plan.argv
    assert "escapes workspace" in plan.refusal_reason if "secrets" in prompt else "unsupported" in plan.refusal_reason
    assert (plan.argv, plan.cwd, plan.authorization) == (None, None, "")
    assert router.route_task(prompt, driver.root).target == "cursor"
    assert router.first_matching_route_id(prompt, driver.root) is None


@pytest.mark.parametrize("prompt,path,keys", P1_REWORK_QUOTED)
def test_p1_rework_quoted_repository_literal_plan(p1_rework_driver, prompt, path, keys):
    import greedy_token.capabilities_invoke as invoke
    import greedy_token.executors as executors
    import greedy_token.router as router

    driver = p1_rework_driver
    route = p1_json_route(path, keys)
    route["argv"] = ["python", "lab/check_users.py", "--path", path, "--keys", ",".join(keys)]
    driver.install(route)
    decision = router.route_task(prompt, driver.root)
    plan = executors.plan_run(decision, prompt, driver.root)
    result = invoke.invoke_capability(driver.root, route["id"], log=False)
    assert decision.intent_slots == router.IntentSlots("json_keys", path, keys)
    assert plan.executable and plan.argv[1:] == tuple(route["argv"][1:]) and plan.cwd == driver.root
    assert result.executed
    driver.execute.assert_called_once()
    actual = driver.execute.call_args.args[0]
    assert actual.argv == plan.argv and actual.cwd == driver.root
    assert router.first_matching_route_id(prompt, driver.root) == route["id"]


@pytest.mark.parametrize("case_id,prompt,expected", P1_REWORK_CONTROLS)
def test_p1_rework_controls(p1_rework_driver, case_id, prompt, expected):
    import greedy_token.executors as executors
    import greedy_token.router as router

    driver = p1_rework_driver
    route = p1_json_route()
    if case_id == "derived":
        route.update(command="python lab/check_users.py", params=["args"], args_from_prompt=[
            {"regex": r'check json keys path="([^"]+)" keys=([A-Za-z_,]+)', "args": '--path "{0}" --keys {1}'},
        ])
    elif expected == "advisory" or case_id == "no-derivation":
        route = p1_commits_route()
    elif expected == "tool":
        route = {"id": "tool-rg-search", "target": "tool", "read_only": True, "patterns": ["find"]}
    driver.install(route)
    decision = router.route_task(prompt, driver.root)
    plan = executors.plan_run(decision, prompt, driver.root)
    if expected == "advisory":
        assert decision.target == "python" and "advisory only" in decision.note
        assert not plan.executable
        assert (plan.command, plan.argv, plan.cwd) == (None, None, None)
        assert router.first_matching_route_id(prompt, driver.root) is None
    elif expected == "tool":
        assert decision.target == "tool" and plan.executable
        assert "each  object" in plan.argv
    else:
        assert decision.target == "python" and plan.executable and plan.cwd == driver.root
        if case_id == "derived":
            assert plan.argv[-4:] == ("--path", "lab/User Data.json", "--keys", "ID,eMail")
        elif case_id != "no-derivation":
            assert plan.argv[-4:] == ("--path", "lab/users.json", "--keys", "id,email")


def test_p1_rework_corpus_identity():
    import hashlib

    corpus = {"version": "p1-rework-v1", "F1": P1_REWORK_CONFLICTS,
              "F1_prompt": "check json keys path=lab/users.json keys=id,email",
              "F2": P1_REWORK_WHITESPACE, "F3": P1_REWORK_DERIVATIONS,
              "F3_spec": [{"regex": r"show (.*)", "args": "{0}"}],
              "F4": P1_REWORK_QUOTED, "controls": P1_REWORK_CONTROLS,
              "oracle": {"F1": "refuse", "F2": "refuse", "F3": "refuse", "F4": "exact-execute"}}
    encoded = json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert sum(map(len, (P1_REWORK_CONFLICTS, P1_REWORK_WHITESPACE, P1_REWORK_DERIVATIONS,
                        P1_REWORK_QUOTED, P1_REWORK_CONTROLS))) == 28
    print(json.dumps({"corpus": corpus, "sha256": hashlib.sha256(encoded).hexdigest()}, sort_keys=True))

