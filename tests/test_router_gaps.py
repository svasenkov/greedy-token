"""Unit tests for router shadow/status/thin-context edge branches (fail_under=100)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import allure
import pytest

import greedy_token.router as router
from greedy_token.router import RouteDecision

pytestmark = [
    allure.epic("Router"),
    allure.parent_suite("Router"),
    allure.feature("Route status"),
    allure.suite("Router gaps"),
]


@allure.title("_parse_shadow_until handles junk, naive, and empty values")
def test_parse_shadow_until() -> None:
    assert router._parse_shadow_until({"shadow_until": "not-a-date"}) is None
    assert router._parse_shadow_until({}) is None
    naive = router._parse_shadow_until({"shadow_until": "2020-01-01T00:00:00"})
    assert naive is not None and naive.tzinfo is not None


@allure.title("_route_status maps shadow window, disabled, and active")
def test_route_status() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert router._route_status({"shadow_until": future}) == "shadow"
    assert router._route_status({"enabled": False}) == "inactive"
    assert router._route_status({}) == "active"


def _decision(**kw) -> RouteDecision:
    base = dict(
        target="tool", route_id="r", confidence=0.9, matched=["m"], command=None,
        note="", domains=[], complexity="low", est_tokens=0, rationale="",
    )
    base.update(kw)
    return RouteDecision(**base)


@allure.title("_apply_thin_context_penalty skips duplicate note/rationale text")
def test_thin_context_penalty_dedup() -> None:
    note = router.THIN_CONTEXT_NOTE
    dec = _decision(note=note, rationale=note)
    out = router._apply_thin_context_penalty(dec, "fix the bug now")
    assert out.note == note
    assert out.rationale == note
    assert out.confidence < dec.confidence


@allure.title("_apply_thin_context_penalty is a no-op for non-cheap tiers or no edit verbs")
def test_thin_context_penalty_noop() -> None:
    assert router._apply_thin_context_penalty(_decision(target="cursor"), "fix it") .target == "cursor"
    assert router._apply_thin_context_penalty(_decision(target="tool"), "just look around").confidence == 0.9


@allure.title("format_decision surfaces shadow match line")
def test_format_decision_shadow(tmp_path: Path) -> None:
    dec = _decision(shadow_route_id="shadow-1", matched=["x"], note="n", rationale="because")
    out = router.format_decision(dec, "task", tmp_path)
    assert "Shadow match (log-only): shadow-1" in out


# --- Mutation kill-tests: exact per-tier/per-field coverage for hot helpers ---


@allure.title("_token_estimate_for_route: exact (complexity, tokens, rationale) per tier")
def test_token_estimate_for_route_exact(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.router import BASE_CURSOR_OVERHEAD, _token_estimate_for_route
    from greedy_token.tokens import count_tokens

    task = "audit skill configurator-boolean"
    task_tokens = count_tokens(task).tokens

    with allure.step("tool → low / 0 / mechanical-search rationale"):
        assert _token_estimate_for_route("tool", task=task, root=minimal_workspace) == (
            "low",
            0,
            "Mechanical search — ripgrep/jq, zero LLM tokens.",
        )
    with allure.step("python → low / 0 / deterministic-script rationale"):
        assert _token_estimate_for_route(
            "python", task=task, root=minimal_workspace
        ) == (
            "low",
            0,
            "Deterministic shell/Python script — no agent context.",
        )
    with allure.step("ollama available → medium / max(task_tokens,1) / cheap-LLM rationale"):
        with patch("greedy_token.router.ollama_available", return_value=True):
            assert _token_estimate_for_route(
                "ollama", task=task, root=minimal_workspace
            ) == (
                "medium",
                max(task_tokens, 1),
                "Cheap LLM — bulk work off expensive path; local/cheap spend.",
            )
            # Empty task → task_tokens 0, so max(0, 1) == 1 kills max(..., 2).
            assert (
                _token_estimate_for_route("ollama", task="", root=minimal_workspace)[1]
                == 1
            )
    with allure.step("ollama unavailable → medium / task_tokens+overhead / fallback rationale"):
        with patch("greedy_token.router.ollama_available", return_value=False):
            assert _token_estimate_for_route(
                "ollama", task=task, root=minimal_workspace
            ) == (
                "medium",
                task_tokens + BASE_CURSOR_OVERHEAD,
                "Cheap LLM unavailable — would fall back to expensive Cursor path.",
            )


@allure.title("_token_estimate_for_route: rag branch threads args and sums tokens")
def test_token_estimate_for_route_rag(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.budget as budget
    import greedy_token.rag_search as rag_search
    from greedy_token.router import RAG_READ_TOKENS_FALLBACK, _token_estimate_for_route
    from greedy_token.tokens import count_tokens

    task = "baseUrl -D flag"
    task_tokens = count_tokens(task).tokens
    seen: dict = {}

    def fake_search_rag(t, r, limit):
        seen["search"] = (t, r, limit)
        return ["hit-1", "hit-2"]

    def fake_rag_est(hits, root):
        seen["est"] = (tuple(hits), root)
        return 123

    monkeypatch.setattr(rag_search, "search_rag", fake_search_rag)
    monkeypatch.setattr(budget, "rag_est_tokens", fake_rag_est)

    with allure.step("hits present → rag_est_tokens(hits, root) + task tokens; exact args"):
        complexity, tokens, rationale = _token_estimate_for_route(
            "rag", task=task, root=minimal_workspace
        )
        assert seen["search"] == (task, minimal_workspace, 5)
        assert seen["est"] == (("hit-1", "hit-2"), minimal_workspace)
        assert complexity == "low"
        assert tokens == 123 + task_tokens
        assert rationale == "Read docs/rag chunk(s) — small context vs full agent chat."

    with allure.step("no hits → RAG_READ_TOKENS_FALLBACK + task tokens"):
        monkeypatch.setattr(rag_search, "search_rag", lambda t, r, limit: [])
        _, tokens_empty, _ = _token_estimate_for_route(
            "rag", task=task, root=minimal_workspace
        )
        assert tokens_empty == RAG_READ_TOKENS_FALLBACK + task_tokens


@allure.title("_token_estimate_for_route: cursor/default branch sums always-on rules")
def test_token_estimate_for_route_cursor(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import greedy_token.context_audit as context_audit
    from greedy_token.router import BASE_CURSOR_OVERHEAD, _token_estimate_for_route
    from greedy_token.tokens import count_tokens

    task = "refactor the header component"
    task_tokens = count_tokens(task).tokens
    seen: dict = {}

    def fake_audit(root):
        seen["root"] = root
        return [
            SimpleNamespace(estimate=SimpleNamespace(tokens=100), always_on=True),
            SimpleNamespace(estimate=SimpleNamespace(tokens=40), always_on=True),
            SimpleNamespace(estimate=SimpleNamespace(tokens=999), always_on=False),
        ]

    monkeypatch.setattr(context_audit, "audit_context", fake_audit)

    with allure.step("cursor → high complexity, rules(always_on)+task+overhead"):
        complexity, tokens, rationale = _token_estimate_for_route(
            "cursor", task=task, root=minimal_workspace
        )
        assert seen["root"] == minimal_workspace  # root threaded, not None
        assert complexity == "high"
        # Only always_on items count: 100 + 40 (the 999 is skipped).
        assert tokens == 140 + task_tokens + BASE_CURSOR_OVERHEAD
        assert rationale == (
            "Wiring/architecture — requires expensive LLM "
            "(Cursor agent chat with rules context)."
        )

    with allure.step("unknown target → default 'medium' complexity, same cursor branch math"):
        complexity_u, tokens_u, _ = _token_estimate_for_route(
            "zzz-unknown", task=task, root=minimal_workspace
        )
        assert complexity_u == "medium"
        assert tokens_u == 140 + task_tokens + BASE_CURSOR_OVERHEAD


@allure.title("_build_tool_command: exact rg command for default and custom route")
def test_build_tool_command_rg_exact(minimal_workspace: Path) -> None:
    from greedy_token.router import _build_tool_command
    from greedy_token.tool_paths import rg_path_for_shell, root_cd_prefix, sh_quote

    prefix = root_cd_prefix(minimal_workspace)
    rg = rg_path_for_shell()

    with allure.step("Default route → default globs / search-paths / max-count 50"):
        globs = ["!.git/**", "!node_modules/**", "!build/**", "!.venv/**", "!.cursor/hooks/**"]
        paths = ["projects", "docs", "stacks", "scripts", "generators"]
        glob_flags = " ".join(f"-g {sh_quote(g)}" for g in globs)
        expected = (
            f"{prefix} {rg} -n --max-columns 200 -F {sh_quote('baseUrl')} "
            f"{glob_flags} --max-count 50 {' '.join(paths)}"
        )
        assert _build_tool_command({}, "find baseUrl", minimal_workspace) == expected

    with allure.step("Custom route keys override defaults (kills key-name/or-default mutants)"):
        route = {"globs": ["!only/**"], "search_paths": ["myproj"], "max_count": 7}
        glob_flags = " ".join(f"-g {sh_quote(g)}" for g in ["!only/**"])
        expected2 = (
            f"{prefix} {rg} -n --max-columns 200 -F {sh_quote('baseUrl')} "
            f"{glob_flags} --max-count 7 myproj"
        )
        assert _build_tool_command(route, "find baseUrl", minimal_workspace) == expected2


@allure.title("_build_tool_command: exact jq command for default and custom route")
def test_build_tool_command_jq_exact(minimal_workspace: Path) -> None:
    from greedy_token.router import _build_tool_command
    from greedy_token.tool_paths import root_cd_prefix, sh_quote

    prefix = root_cd_prefix(minimal_workspace)

    with allure.step("Default jq route → '.' filter, phase-manifest path"):
        expected = (
            f"{prefix} jq -r {sh_quote('.')} {sh_quote('docs/phase-manifest.json')}"
        )
        assert _build_tool_command({"tool": "jq"}, "task", minimal_workspace) == expected

    with allure.step("Custom jq route → custom filter and json_path"):
        route = {"tool": "jq", "jq_filter": ".items[]", "json_path": "data/x.json"}
        expected2 = f"{prefix} jq -r {sh_quote('.items[]')} {sh_quote('data/x.json')}"
        assert _build_tool_command(route, "task", minimal_workspace) == expected2


@allure.title("_decision_from_route: exact fields for a tool route (note appended)")
def test_decision_from_route_tool_fields(minimal_workspace: Path) -> None:
    from greedy_token.router import _build_tool_command, _decision_from_route

    route = {
        "id": "tool-rg",
        "target": "tool",
        "patterns": ["find"],
        "note": "  extra note  ",
        "domains": ["config"],
        "tool": "rg",
    }
    dec = _decision_from_route(
        route, score=1.0, matched=["find"], task="find baseUrl", root=minimal_workspace
    )
    with allure.step("confidence = min(0.95, 0.45 + 1.0*0.12) = 0.57"):
        assert dec.confidence == pytest.approx(0.57)
    with allure.step("every field is exact"):
        assert dec.target == "tool"
        assert dec.route_id == "tool-rg"
        assert dec.matched == ["find"]
        assert dec.command == _build_tool_command(route, "find baseUrl", minimal_workspace)
        assert dec.read_only is True
        assert dec.note == "extra note"
        assert dec.domains == ["config"]
        assert dec.complexity == "low"
        assert dec.est_tokens == 0
        assert dec.tool == "rg"
        # note (not already in rationale) is appended to the mechanical-search rationale.
        assert dec.rationale == (
            "Mechanical search — ripgrep/jq, zero LLM tokens. extra note"
        )


@allure.title("_decision_from_route: confidence cap and note-dedup branch logic")
def test_decision_from_route_confidence_and_note(minimal_workspace: Path) -> None:
    from greedy_token.router import _decision_from_route

    with allure.step("High score caps confidence at 0.95"):
        route = {"id": "r", "target": "python", "patterns": [], "domains": []}
        dec = _decision_from_route(
            route, score=10.0, matched=[], task="do it", root=minimal_workspace
        )
        assert dec.confidence == 0.95

    with allure.step("Empty note (missing key) → rationale unchanged, note ''"):
        base = _decision_from_route(
            {"id": "r", "target": "python", "patterns": []},
            score=1.0,
            matched=[],
            task="do it",
            root=minimal_workspace,
        )
        assert base.note == ""
        assert base.rationale == "Deterministic shell/Python script — no agent context."

    with allure.step("note already inside rationale → NOT re-appended (kills and/not-in mutants)"):
        route2 = {
            "id": "r2",
            "target": "python",
            "patterns": [],
            "note": "Deterministic shell/Python script — no agent context.",
        }
        dec2 = _decision_from_route(
            route2, score=1.0, matched=[], task="do it", root=minimal_workspace
        )
        assert dec2.rationale == "Deterministic shell/Python script — no agent context."


@allure.title("_decision_from_route: ollama wrapper unavailable appends note to rationale")
def test_decision_from_route_ollama_wrapper(minimal_workspace: Path) -> None:
    from greedy_token.router import _decision_from_route

    route = {
        "id": "oll",
        "target": "ollama",
        "patterns": [],
        "command": "scripts/ollama/audit-skill.sh",
    }
    with allure.step("wrapper.requires_ollama and ollama down → rationale gains availability note"):
        with patch("greedy_token.router.ollama_available", return_value=False):
            dec = _decision_from_route(
                route, score=1.0, matched=[], task="t", root=minimal_workspace
            )
        assert dec.rationale.endswith("Ollama optional but currently unavailable.")
        assert dec.rationale.startswith("Cheap LLM unavailable")
    with allure.step("ollama available → no availability note appended"):
        with patch("greedy_token.router.ollama_available", return_value=True):
            dec2 = _decision_from_route(
                route, score=1.0, matched=[], task="t", root=minimal_workspace
            )
        assert "Ollama optional but currently unavailable." not in dec2.rationale


@allure.title("_fallback_for_tier: exact RouteDecision fields for cursor and non-cursor tiers")
def test_fallback_for_tier_exact(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.router import _fallback_for_tier, _token_estimate_for_route

    with allure.step("the real root is threaded into the token estimate (not None)"):
        seen: dict = {}
        orig_est = router._token_estimate_for_route

        def spy(target, *, task, root):
            seen["root"] = root
            return orig_est(target, task=task, root=root)

        monkeypatch.setattr(router, "_token_estimate_for_route", spy)
        _fallback_for_tier("cursor", "task", minimal_workspace, {})
        assert seen["root"] == minimal_workspace
        monkeypatch.undo()

    with allure.step("cursor tier → cursor-fallback id, 0.35 confidence, first message line"):
        cfg = {"cursor_fallback": {"message": "Open a chat.\nsecond line"}}
        dec = _fallback_for_tier("cursor", "task", minimal_workspace, cfg)
        assert dec.target == "cursor"
        assert dec.route_id == "cursor-fallback"
        assert dec.confidence == 0.35
        assert dec.matched == []
        assert dec.command is None
        assert dec.note == ""
        assert dec.domains == []
        assert dec.complexity == "high"
        assert dec.rationale == "Open a chat."  # first line only
        # est_tokens is the root-dependent cursor estimate (kills root=None + kwarg drop).
        expected_cursor_est = _token_estimate_for_route(
            "cursor", task="task", root=minimal_workspace
        )[1]
        assert dec.est_tokens == expected_cursor_est
        assert dec.est_tokens > 0

    with allure.step("cursor tier + cfg without cursor_fallback → estimate rationale, no crash"):
        dec_nofb = _fallback_for_tier("cursor", "task", minimal_workspace, {})
        assert dec_nofb.route_id == "cursor-fallback"
        assert dec_nofb.rationale.startswith("Wiring/architecture")

    with allure.step("non-cursor tier → '<tier>-none' id, 0.0 confidence, no-match rationale"):
        dec2 = _fallback_for_tier("python", "task", minimal_workspace, {})
        assert dec2.target == "python"
        assert dec2.route_id == "python-none"
        assert dec2.confidence == 0.0
        assert dec2.matched == []
        assert dec2.command is None
        assert dec2.note == ""
        assert dec2.domains == []
        assert dec2.complexity == "low"
        assert dec2.rationale == "No pattern match in tier."

    with allure.step("rag tier fallback carries a nonzero est_tokens (kills kwarg drop)"):
        dec_rag = _fallback_for_tier("rag", "baseUrl", minimal_workspace, {})
        expected_rag_est = _token_estimate_for_route(
            "rag", task="baseUrl", root=minimal_workspace
        )[1]
        assert dec_rag.est_tokens == expected_rag_est
        assert dec_rag.est_tokens > 0


@allure.title("_decision_from_route: read_only, command threading, and root passthrough")
def test_decision_from_route_non_tool_fields(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from greedy_token.router import BASE_CURSOR_OVERHEAD, _decision_from_route
    from greedy_token.tokens import count_tokens

    with allure.step("python route: read_only from route key, command passthrough (not rebuilt)"):
        route = {
            "id": "py",
            "target": "python",
            "patterns": [],
            "read_only": True,
            "command": "python scripts/x.py",
        }
        dec = _decision_from_route(
            route, score=1.0, matched=[], task="do", root=minimal_workspace
        )
        assert dec.read_only is True
        assert dec.command == "python scripts/x.py"

    with allure.step("python route without read_only key → default False for non-tool tier"):
        dec_ro = _decision_from_route(
            {"id": "py", "target": "python", "patterns": []},
            score=1.0,
            matched=[],
            task="do",
            root=minimal_workspace,
        )
        assert dec_ro.read_only is False

    with allure.step("root is threaded into _token_estimate_for_route (not None)"):
        seen: dict = {}
        orig = router._token_estimate_for_route

        def spy(target, *, task, root):
            seen["root"] = root
            return orig(target, task=task, root=root)

        monkeypatch.setattr(router, "_token_estimate_for_route", spy)
        _decision_from_route(
            {"id": "py", "target": "python", "patterns": []},
            score=1.0,
            matched=[],
            task="do",
            root=minimal_workspace,
        )
        assert seen["root"] == minimal_workspace

    with allure.step("nonzero est_tokens is threaded (kills est_tokens kwarg removal)"):
        task = "wire up the thing"
        with patch("greedy_token.router.ollama_available", return_value=False):
            dec_est = _decision_from_route(
                {"id": "oll", "target": "ollama", "patterns": []},
                score=1.0,
                matched=[],
                task=task,
                root=minimal_workspace,
            )
        assert dec_est.est_tokens == count_tokens(task).tokens + BASE_CURSOR_OVERHEAD
        assert dec_est.est_tokens != 0


@allure.title("_score_search_token: exact per-branch scores")
def test_score_search_token_exact() -> None:
    from greedy_token.router import _score_search_token

    with allure.step("length is capped at 24"):
        assert _score_search_token("a" * 30) == 24
    with allure.step("camelCase adds +12"):
        assert _score_search_token("aB") == 14  # min(2,24)=2 + 12
    with allure.step("ALLCAPS len<=2 does NOT get the +6 (and, strictly > 2)"):
        assert _score_search_token("AB") == 2
    with allure.step("ALLCAPS len>2 gets +6"):
        assert _score_search_token("ABC") == 9  # 3 + 6
    with allure.step("no bonus token"):
        assert _score_search_token("Xab") == 3
    with allure.step("path-ish char adds +4"):
        assert _score_search_token("a.b") == 7  # 3 + 4
        assert _score_search_token("a.-_/b") == 10  # 6 + 4
    with allure.step("pure digits subtract 8"):
        assert _score_search_token("12") == -6  # 2 - 8


@allure.title("_extract_search_query: quoted, filler, length, and sort branches")
def test_extract_search_query_branches() -> None:
    from greedy_token.router import _extract_search_query

    with allure.step("quoted phrase wins verbatim"):
        assert _extract_search_query('find "hello world" now') == "hello world"
    with allure.step("filler word filtered even when it would outscore the keeper"):
        # 'configurator' is filler (dropped); 'ab' is the only real candidate.
        assert _extract_search_query("find configurator ab") == "ab"
    with allure.step("two short tokens both filtered → fall back to whole text"):
        assert _extract_search_query("find ab cd") == "ab"  # both kept (len 2 not < 2)
    with allure.step("equal score, longer token wins (sort by -len)"):
        # 'aB' scores 14 (camel); 14 lowercase chars also score 14 → longer wins.
        long_tok = "abcdefghijklmn"
        assert _extract_search_query(f"find aB {long_tok}") == long_tok
    with allure.step("stray quotes stripped down to empty fall-through text"):
        assert _extract_search_query("find \"'\"") == ""
    with allure.step("leading double-quote stripped (kills the '\"' strip-set mutant)"):
        # 'X' becomes the whole fall-through text once the leading double-quote is
        # stripped; a strip('XX\"XX') would also eat the 'X'.
        assert _extract_search_query('find "X') == "X"
    with allure.step("leading single-quote stripped (kills the \"'\" strip-set mutant)"):
        assert _extract_search_query("find 'X") == "X"


@allure.title("_strip_search_prefix: strips known prefix case-insensitively")
def test_strip_search_prefix() -> None:
    from greedy_token.router import _strip_search_prefix

    assert _strip_search_prefix("find foo") == "foo"
    assert _strip_search_prefix("FIND foo") == "foo"  # IGNORECASE flag
    assert _strip_search_prefix("no prefix here") == "no prefix here"


@allure.title("_normalize: collapses whitespace and lowercases")
def test_normalize() -> None:
    from greedy_token.router import _normalize

    assert _normalize("  A   B  ") == "a b"


@allure.title("_route_status: strict < boundary at the shadow_until instant")
def test_route_status_boundary() -> None:
    fixed = datetime(2026, 7, 1, tzinfo=timezone.utc)
    route = {"shadow_until": fixed.isoformat()}
    with patch("greedy_token.router._now", return_value=fixed):
        # now == until → strict < is False → not 'shadow' (kills <=).
        assert router._route_status(route) != "shadow"


@allure.title("_best_in_tier: None default, empty-pattern selection, missing-patterns skip")
def test_best_in_tier_edges(minimal_workspace: Path) -> None:
    from greedy_token.router import _best_in_tier

    with allure.step("no routes → None (not '')"):
        assert _best_in_tier([], "text", "task", minimal_workspace) is None

    with allure.step("empty-pattern route scores exactly 1.0 and is still selected"):
        routes = [{"id": "r", "target": "tool", "patterns": [""]}]
        best = _best_in_tier(routes, "anything", "task", minimal_workspace)
        assert best is not None
        assert best.route_id == "r"

    with allure.step("inactive first route uses continue, not break"):
        routes = [
            {"id": "off", "target": "tool", "patterns": [""], "enabled": False},
            {"id": "on", "target": "tool", "patterns": [""]},
        ]
        best2 = _best_in_tier(routes, "anything", "task", minimal_workspace)
        assert best2 is not None
        assert best2.route_id == "on"

    with allure.step("route missing 'patterns' key defaults to [] and is skipped"):
        assert _best_in_tier(
            [{"id": "r", "target": "tool"}], "text", "task", minimal_workspace
        ) is None


@allure.title("_best_shadow_match: None default and first-wins on tie (strict >)")
def test_best_shadow_match_edges() -> None:
    from greedy_token.router import _best_shadow_match

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with allure.step("no shadow routes → (None, 0.0)"):
        assert _best_shadow_match([], "text") == (None, 0.0)

    with allure.step("equal-score shadow routes → first one wins (strict >, not >=)"):
        routes = [
            {"id": "first", "shadow_until": future, "patterns": ["alpha"]},
            {"id": "second", "shadow_until": future, "patterns": ["alpha"]},
        ]
        best_id, score = _best_shadow_match(routes, "alpha")
        assert best_id == "first"
        assert score > 0.0

    with allure.step("shadow route missing 'patterns' key defaults to [] (no crash, no match)"):
        assert _best_shadow_match(
            [{"id": "s", "shadow_until": future}], "alpha"
        ) == (None, 0.0)


def _rich_decision(**kw) -> RouteDecision:
    # complexity is deliberately NOT the dataclass default ("medium") so that a
    # dropped `complexity=` kwarg at a RouteDecision call site is observable.
    base = dict(
        target="rag", route_id="rid", confidence=0.77, matched=["m1", "m2"],
        command="the-command", note="the-note", domains=["config", "testing"],
        complexity="high", est_tokens=321, rationale="the-rationale",
        read_only=True, tool="rg", shadow_route_id="prev-shadow",
    )
    base.update(kw)
    return RouteDecision(**base)


@allure.title("_with_shadow: copies every field verbatim and sets shadow id")
def test_with_shadow_copies_all_fields() -> None:
    dec = _rich_decision(shadow_route_id=None)
    out = router._with_shadow(dec, "shadow-new")
    with allure.step("shadow id applied; all other fields preserved"):
        assert out.shadow_route_id == "shadow-new"
        assert out.target == "rag"
        assert out.route_id == "rid"
        assert out.confidence == 0.77
        assert out.matched == ["m1", "m2"]
        assert out.command == "the-command"
        assert out.note == "the-note"
        assert out.domains == ["config", "testing"]
        assert out.complexity == "high"
        assert out.est_tokens == 321
        assert out.rationale == "the-rationale"
        assert out.read_only is True
        assert out.tool == "rg"


@allure.title("_with_shadow: returns decision unchanged for falsy id or equal id")
def test_with_shadow_short_circuits() -> None:
    with allure.step("falsy shadow id → decision returned unchanged (kills or→and)"):
        dec = _rich_decision(shadow_route_id="keep-me")
        assert router._with_shadow(dec, "") is dec
    with allure.step("same shadow id already present → returned unchanged"):
        dec2 = _rich_decision(shadow_route_id="same")
        assert router._with_shadow(dec2, "same") is dec2


@allure.title("_apply_thin_context_penalty: rag tier penalty and full field copy")
def test_apply_thin_context_penalty_rag_fields() -> None:
    dec = _rich_decision(
        target="rag", confidence=0.9, note="", rationale="base rationale",
        shadow_route_id="sh",
    )
    out = router._apply_thin_context_penalty(dec, "fix the bug now")
    with allure.step("rag tier IS penalised: confidence = max(0.15, 0.9 - 0.35) = 0.55"):
        assert out.confidence == pytest.approx(0.55)
    with allure.step("note becomes exactly the thin-context note (leading '; ' stripped)"):
        assert out.note == router.THIN_CONTEXT_NOTE
    with allure.step("thin-context note appended to rationale"):
        assert out.rationale == f"base rationale {router.THIN_CONTEXT_NOTE}"
    with allure.step("all identity fields preserved"):
        assert out.target == "rag"
        assert out.route_id == "rid"
        assert out.matched == ["m1", "m2"]
        assert out.command == "the-command"
        assert out.domains == ["config", "testing"]
        assert out.complexity == "high"
        assert out.est_tokens == 321
        assert out.read_only is True
        assert out.tool == "rg"
        assert out.shadow_route_id == "sh"


@allure.title("_apply_thin_context_penalty: only leading '; ' is stripped from the note")
def test_apply_thin_context_penalty_note_strip() -> None:
    # A prior note that starts with a non-'; ' char must survive verbatim; only the
    # joining "; " prefix is stripped. strip("XX; XX") would also eat a leading 'X'.
    dec = _rich_decision(target="tool", confidence=0.9, note="Xkeep")
    out = router._apply_thin_context_penalty(dec, "refactor this")
    assert out.note.startswith("Xkeep; ")
    assert router.THIN_CONTEXT_NOTE in out.note


@allure.title("_runner_up: skips selected tier, threads root, falls back to cursor")
def test_runner_up_branches(minimal_workspace: Path) -> None:
    from greedy_token.router import _runner_up

    with allure.step("threads real root and returns a different tier than selected"):
        seen: dict = {}
        orig = router.route_task_all_tiers

        def spy(task, root):
            seen["root"] = root
            return orig(task, root)

        with patch.object(router, "route_task_all_tiers", spy):
            runner = _runner_up("find baseUrl", minimal_workspace, "tool")
        assert seen["root"] == minimal_workspace
        if runner is not None:
            assert runner[0] != "tool"

    with allure.step("open-ended task selected as cursor → runner-up falls back to cursor row"):
        runner2 = _runner_up(
            "explain quantum foam in repository layout", minimal_workspace, "python"
        )
        assert runner2 is not None
        assert runner2[0] == "cursor"

    with allure.step("selected tier skipped with continue (not break) → later matched tier wins"):
        matched = _rich_decision(matched=["m"])
        nomatch = _rich_decision(matched=[])
        scan = [
            ("tool", matched),  # == selected_target → must be skipped, loop continues
            ("python", nomatch),
            ("rag", _rich_decision(matched=["r"], route_id="rag-route")),
            ("cursor", _rich_decision(matched=[], route_id="cursor-fallback")),
        ]
        with patch.object(router, "route_task_all_tiers", lambda task, root: scan):
            runner3 = _runner_up("t", minimal_workspace, "tool")
        assert runner3 is not None
        assert runner3[0] == "rag"  # break would have jumped to the cursor fallback
