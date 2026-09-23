"""Step-4 auditable crystallization lifecycle: candidate → proposed → approved → applied.

The lifecycle never grants execution authority by itself — ``promote`` (the
apply transition) passes the approved draft bytes through Step-2 trust
(``approve_script``), and refuses when the draft changed after approval.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import allure
import greedy_token.cli as cli
import greedy_token.crystallize_l3 as l3
from greedy_token.capabilities import capability_by_id, collect_capabilities
from greedy_token.crystal_ids import crystal_id_for_pattern
from greedy_token.hub.crystallize import (
    append_lifecycle_event,
    crystal_states,
    derive_crystal_state,
    list_crystals,
    load_lifecycle_events,
)
from greedy_token.paths import upsert_workspace_routes, workspace_config_routes
from greedy_token.trust import (
    approve_script,
    trusted_manifest_paths,
    verify_trust_manifest,
)
from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("Crystallize"),
    allure.parent_suite("Crystallize"),
    allure.feature("Lifecycle audit"),
    allure.suite("Crystallize lifecycle"),
]

TASK = "summarize weekly spend report table"
CRYSTAL_ID = crystal_id_for_pattern(TASK)


def _ns(**kwargs) -> Namespace:
    defaults = {"no_log": True, "json": False, "since": "30d", "by": "", "reason": ""}
    defaults.update(kwargs)
    return Namespace(**defaults)


def _lifecycle_events(crystal_id: str = CRYSTAL_ID) -> list[dict]:
    return [e for e in load_lifecycle_events() if e.get("crystal_id") == crystal_id]


def _draft_rel(crystal_id: str = CRYSTAL_ID) -> str:
    return f".greedy-token/drafts/{crystal_id}.py"


# ---------------------------------------------------------------- derived state


@allure.story("State derivation")
@allure.title("crystal_states derives the funnel and never moves state back on watch/report")
def test_derive_states_funnel(crystal_home: Path) -> None:
    cid = "python-foo-bar"
    assert derive_crystal_state(cid)["state"] == "unknown"

    append_lifecycle_event(stage="watch", crystal_id=cid)
    assert derive_crystal_state(cid)["state"] == "candidate"

    append_lifecycle_event(stage="draft", crystal_id=cid)
    append_lifecycle_event(stage="shadow", crystal_id=cid)
    assert derive_crystal_state(cid)["state"] == "proposed"

    append_lifecycle_event(
        stage="approved", crystal_id=cid, actor="alice", reason="reviewed"
    )
    info = derive_crystal_state(cid)
    assert info["state"] == "approved"
    assert info["approved"]["actor"] == "alice"
    assert info["approved"]["reason"] == "reviewed"

    append_lifecycle_event(stage="promoted", crystal_id=cid)
    assert derive_crystal_state(cid)["state"] == "applied"

    # Observational stages never roll the state back.
    append_lifecycle_event(stage="watch", crystal_id=cid)
    assert derive_crystal_state(cid)["state"] == "applied"

    append_lifecycle_event(stage="rejected", crystal_id=cid)
    assert derive_crystal_state(cid)["state"] == "rejected"
    assert derive_crystal_state(cid)["approved"] is None


@allure.story("State derivation")
@allure.title("re-draft after reject re-proposes and clears the old approval pin")
def test_derive_redraft_resets_approval(crystal_home: Path) -> None:
    cid = "python-foo-baz"
    for stage in ("draft", "shadow", "approved", "rejected", "draft", "shadow"):
        append_lifecycle_event(stage=stage, crystal_id=cid)
    info = derive_crystal_state(cid)
    assert info["state"] == "proposed"
    assert info["approved"] is None


@allure.story("State derivation")
@allure.title("crystal_states maps every crystal in one pass")
def test_crystal_states_one_pass(crystal_home: Path) -> None:
    append_lifecycle_event(stage="watch", crystal_id="python-a-one")
    append_lifecycle_event(stage="draft", crystal_id="python-b-two")
    append_lifecycle_event(stage="approved", crystal_id="python-c-three")
    states = crystal_states()
    assert states["python-a-one"]["state"] == "candidate"
    assert states["python-b-two"]["state"] == "proposed"
    assert states["python-c-three"]["state"] == "approved"


# ---------------------------------------------------------------- approve


@allure.story("Approve")
@allure.title("approve pins the reviewed draft sha256 and logs actor/reason")
def test_approve_pins_sha_and_actor(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace, actor="bob")
    result = l3.approve_crystal(
        CRYSTAL_ID, root=minimal_workspace, actor="alice", reason="looked fine"
    )
    attach_text("approve", json.dumps(result))
    assert result["state"] == "approved"
    assert result["actor"] == "alice"
    assert result["approved_sha256"] == l3.sha256_file(
        l3.draft_path(minimal_workspace, CRYSTAL_ID)
    )
    event = _lifecycle_events()[-1]
    assert event["stage"] == "approved"
    assert event["actor"] == "alice"
    assert event["reason"] == "looked fine"
    assert event["transition"] == "proposed->approved"
    assert derive_crystal_state(CRYSTAL_ID)["state"] == "approved"


@allure.story("Approve")
@allure.title("approve refuses: no proposal, applied, rejected, missing draft")
def test_approve_refusals(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    with pytest.raises(ValueError, match="no proposal to approve"):
        l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)

    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    with pytest.raises(ValueError, match="already applied"):
        l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)

    # rejected → must re-draft first
    l3.reject_crystal(CRYSTAL_ID, root=minimal_workspace)
    with pytest.raises(ValueError, match="rejected"):
        l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)

    # proposed but draft file removed on disk → refuse
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.draft_path(minimal_workspace, CRYSTAL_ID).unlink()
    with pytest.raises(ValueError, match="draft script missing"):
        l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)


@allure.story("Approve")
@allure.title("approve defaults actor to the local user when --by is empty")
def test_approve_default_actor(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    result = l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert result["actor"]  # env user / GREEDY_TOKEN_ACTOR / local-cli
    assert _lifecycle_events()[-1]["actor"] == result["actor"]


# ---------------------------------------------------------------- promote (apply)


@allure.story("Promote")
@allure.title("promote requires an approved proposal — proposed alone is refused")
def test_promote_requires_approval(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    with pytest.raises(ValueError, match="no proposal"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    with pytest.raises(ValueError, match="not approved"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)


@allure.story("Promote")
@allure.title("promote applies through Step-2 trust: manifest entry + active route + event")
def test_promote_applies_via_trust(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace, actor="alice")
    result = l3.promote_crystal(
        CRYSTAL_ID, root=minimal_workspace, actor="carol", reason="ship it"
    )
    attach_text("promote", json.dumps(result))

    rel = _draft_rel()
    assert result["state"] == "applied"
    assert result["trusted"] == f"manifest:{rel}"
    assert rel in trusted_manifest_paths(minimal_workspace)

    checks = verify_trust_manifest(minimal_workspace)
    check = next(c for c in checks if c.entry.path == rel)
    assert check.ok
    assert check.entry.approval_source == "crystallize-promote"

    route = next(
        r for r in workspace_config_routes(minimal_workspace) if r["id"] == CRYSTAL_ID
    )
    assert "shadow_until" not in route
    assert "enabled" not in route

    event = _lifecycle_events()[-1]
    assert event["stage"] == "promoted"
    assert event["actor"] == "carol"
    assert event["reason"] == "ship it"
    assert event["transition"] == "approved->applied"
    assert event["sha256"] == result["sha256"]


@allure.story("Promote")
@allure.title("promote refuses when the draft changed after approval (stale pin)")
def test_promote_stale_pin_refuses(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    draft = l3.draft_path(minimal_workspace, CRYSTAL_ID)
    draft.write_text(draft.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since approval"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    # Re-approving the new bytes unblocks apply.
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    result = l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert result["state"] == "applied"


@allure.story("Promote")
@allure.title("promote refuses an approval that carries no sha256 pin")
def test_promote_requires_sha_pin(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    append_lifecycle_event(stage="approved", crystal_id=CRYSTAL_ID, actor="legacy")
    with pytest.raises(ValueError, match="approved_sha256"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert not trusted_manifest_paths(minimal_workspace)


@allure.story("Promote")
@allure.title("promote under a compare/apply race never binds bytes the approval did not pin")
def test_promote_race_binds_only_pinned_bytes(
    minimal_workspace: Path,
    crystal_home: Path,
    no_cheap_llm: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    draft = l3.draft_path(minimal_workspace, CRYSTAL_ID)
    real_approve_script = l3.approve_script

    def concurrent_edit(root: Path, path: str, **kwargs: object) -> object:
        draft.write_text(draft.read_text(encoding="utf-8") + "\n# raced\n")
        return real_approve_script(root, path, **kwargs)

    monkeypatch.setattr(l3, "approve_script", concurrent_edit)
    with pytest.raises(ValueError, match="changed while it was being applied"):
        l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    # The raced bytes are revoked, the route stays shadow, state stays approved.
    assert not trusted_manifest_paths(minimal_workspace)
    assert derive_crystal_state(CRYSTAL_ID)["state"] == "approved"
    route = next(
        r for r in workspace_config_routes(minimal_workspace) if r["id"] == CRYSTAL_ID
    )
    assert "shadow_until" in route

    # Re-approving the raced bytes unblocks apply (undo() would also roll back
    # the env fixtures — restore the patched attr instead).
    monkeypatch.setattr(l3, "approve_script", real_approve_script)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)["state"] == "applied"


@allure.story("Promote")
@allure.title("approval is workspace-bound: an approval from workspace A cannot apply in B")
def test_promote_refuses_foreign_workspace_approval(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace, actor="reviewer-a")

    # A second workspace with the identical draft bytes + shadow route: only the
    # workspace binding differs, so promote must still refuse.
    second = minimal_workspace / "workspace-b"
    draft_b = l3.draft_path(second, CRYSTAL_ID)
    draft_b.parent.mkdir(parents=True, exist_ok=True)
    draft_b.write_text(
        l3.draft_path(minimal_workspace, CRYSTAL_ID).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    upsert_workspace_routes(
        second, {"routes": [l3._shadow_route(CRYSTAL_ID, TASK, l3._shadow_until_iso())]}
    )
    with pytest.raises(ValueError, match="different workspace"):
        l3.promote_crystal(CRYSTAL_ID, root=second)
    assert not trusted_manifest_paths(second)
    foreign = l3.crystal_status(CRYSTAL_ID, root=second)
    assert foreign["state"] == "approved"  # shared lifecycle log still shows it
    assert foreign["approved"]["workspace_match"] is False

    # Re-approving inside the target workspace is the supported recovery.
    l3.approve_crystal(CRYSTAL_ID, root=second)
    result = l3.promote_crystal(CRYSTAL_ID, root=second)
    assert result["state"] == "applied"
    assert trusted_manifest_paths(second)


@allure.story("Promote")
@allure.title("applied crystal is executable: capability ready + invoke runs the trusted draft")
def test_applied_crystal_is_invocable(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    cap = capability_by_id(minimal_workspace, CRYSTAL_ID)
    assert cap is not None
    assert cap.readiness == "disabled_or_shadow"
    assert cap.lifecycle_state == "proposed"

    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)

    cap = capability_by_id(minimal_workspace, CRYSTAL_ID)
    assert cap is not None
    assert cap.lifecycle_state == "applied"
    assert cap.readiness == "ready"
    assert cap.invocable

    from greedy_token.capabilities import invoke_capability

    result = invoke_capability(minimal_workspace, CRYSTAL_ID, log=False)
    assert result.executed is True
    assert result.exit_code == 0
    assert "crystal_id" in result.output


# ---------------------------------------------------------------- reject


@allure.story("Reject")
@allure.title("reject removes draft + route + trust entry and logs who/why")
def test_reject_revokes_trust(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert _draft_rel() in trusted_manifest_paths(minimal_workspace)

    result = l3.reject_crystal(
        CRYSTAL_ID, root=minimal_workspace, actor="dave", reason="overrides"
    )
    attach_text("reject", json.dumps(result))
    assert result["revoked_trust"] is True
    assert _draft_rel() not in trusted_manifest_paths(minimal_workspace)
    event = _lifecycle_events()[-1]
    assert event["actor"] == "dave"
    assert event["reason"] == "overrides"
    assert event["transition"] == "applied->rejected"
    assert derive_crystal_state(CRYSTAL_ID)["state"] == "rejected"


@allure.story("Reject")
@allure.title("reject revokes every trust path the candidate bound — incl. a repointed route")
def test_reject_revokes_all_candidate_trust_paths(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    # Route command repointed at another script (same bytes) before promote.
    draft = l3.draft_path(minimal_workspace, CRYSTAL_ID)
    alternate = minimal_workspace / "scripts" / "alternate-check.py"
    alternate.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
    route = next(
        r for r in workspace_config_routes(minimal_workspace) if r["id"] == CRYSTAL_ID
    )
    route["command"] = "python scripts/alternate-check.py"
    upsert_workspace_routes(minimal_workspace, {"routes": [route]})

    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert "scripts/alternate-check.py" in trusted_manifest_paths(minimal_workspace)

    append_lifecycle_event(stage="watch", crystal_id="python-other-one")
    rejected = l3.reject_crystal(CRYSTAL_ID, root=minimal_workspace)
    attach_text("reject", json.dumps(rejected))
    assert rejected["revoked_trust"] is True
    assert rejected["revoked_paths"] == ["scripts/alternate-check.py"]
    assert not trusted_manifest_paths(minimal_workspace)


@allure.story("Reject")
@allure.title("reject tolerates junk paths in lifecycle events and routes without a script")
def test_reject_tolerates_junk_paths(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    append_lifecycle_event(
        stage="promoted",
        crystal_id=CRYSTAL_ID,
        extra={"script_path": "../outside.py", "trusted": "manifest:/abs/x.py"},
    )
    route = next(
        r for r in workspace_config_routes(minimal_workspace) if r["id"] == CRYSTAL_ID
    )
    route["command"] = "echo no-script-here"
    upsert_workspace_routes(minimal_workspace, {"routes": [route]})

    rejected = l3.reject_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert rejected["ok"] is True
    assert rejected["revoked_paths"] == []


@allure.story("Reject")
@allure.title("reject validates the id before touching the filesystem (no path traversal)")
def test_reject_validates_crystal_id(
    minimal_workspace: Path, crystal_home: Path
) -> None:
    victim = minimal_workspace / "audit-created-fixture.py"
    victim.write_text("AUDIT_FIXTURE = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid crystal id"):
        l3.reject_crystal("../../audit-created-fixture", root=minimal_workspace)
    assert victim.exists()


# ---------------------------------------------------------------- draft guards


@allure.story("Draft")
@allure.title("draft on an applied crystal is refused — no silent demotion to shadow")
def test_draft_refuses_when_applied(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    with pytest.raises(ValueError, match="already applied"):
        l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)


@allure.story("Draft")
@allure.title("draft events record the actor")
def test_draft_records_actor(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace, actor="eve")
    stages = [(e["stage"], e.get("actor")) for e in _lifecycle_events()]
    assert stages == [("draft", "eve"), ("shadow", "eve")]


# ---------------------------------------------------------------- status / candidates


@allure.story("Status")
@allure.title("crystal_status: derived state + draft/route/trust facts + timeline + next hint")
def test_crystal_status_full_flow(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    status = l3.crystal_status(CRYSTAL_ID, root=minimal_workspace)
    assert status["state"] == "candidate"
    assert "draft" in status["next"]

    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    status = l3.crystal_status(CRYSTAL_ID, root=minimal_workspace)
    attach_text("status-proposed", json.dumps(status))
    assert status["state"] == "proposed"
    assert status["draft"]["exists"] is True
    assert status["draft"]["sha256"]
    assert status["route"]["status"] == "shadow"
    assert status["trust"]["approved"] is False
    assert "approve" in status["next"]

    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace, actor="alice")
    l3.promote_crystal(CRYSTAL_ID, root=minimal_workspace)
    status = l3.crystal_status(CRYSTAL_ID, root=minimal_workspace)
    attach_text("status-applied", json.dumps(status))
    assert status["state"] == "applied"
    assert status["route"]["status"] == "active"
    assert status["trust"]["approved"] is True
    assert status["trust"]["check"] == "ok"
    assert status["approved"]["actor"] == "alice"
    assert [e["stage"] for e in status["timeline"]] == [
        "draft",
        "shadow",
        "approved",
        "promoted",
    ]


@allure.story("Status")
@allure.title("crystal_status on an unknown id reports unknown + candidates hint")
def test_crystal_status_unknown(minimal_workspace: Path, crystal_home: Path) -> None:
    status = l3.crystal_status("python-no-such-op", root=minimal_workspace)
    assert status["state"] == "unknown"
    assert "candidates" in status["next"]


@allure.story("Status")
@allure.title("crystal_status resolves routes_file + trust facts for non-crystal route ids")
def test_crystal_status_non_crystal_route(
    minimal_workspace: Path, crystal_home: Path
) -> None:
    # python-meta-sync-check lives in workspace-routes.yaml (routes_file), not
    # inline in .greedy-token.yaml — status must still see it.
    status = l3.crystal_status("python-meta-sync-check", root=minimal_workspace)
    assert status["route"]["present"] is True
    assert status["route"]["status"] == "active"
    assert status["trust"]["approved"] is False

    approve_script(minimal_workspace, "scripts/meta-sync-check.py")
    status = l3.crystal_status("python-meta-sync-check", root=minimal_workspace)
    attach_text("status-routes_file-trusted", json.dumps(status))
    assert status["route"]["present"] is True
    assert status["trust"]["approved"] is True
    assert status["trust"]["check"] == "ok"


@allure.story("Candidates")
@allure.title("list_crystals annotates entries with the derived state")
def test_list_crystals_state_annotation(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    listing = list_crystals(since="30d")
    entry = next(c for c in listing["crystals"] if c["crystal_id"] == CRYSTAL_ID)
    assert entry["state"] == "proposed"
    assert entry["latest_stage"] == "shadow"


# ---------------------------------------------------------------- capabilities surface


@allure.story("Capabilities")
@allure.title("lifecycle verbs are visible as capabilities — never invocable")
def test_lifecycle_ops_in_capabilities(minimal_workspace: Path) -> None:
    view = collect_capabilities(minimal_workspace)
    ops = {op.id: op for op in view.ops}
    for op_id in (
        "crystallize-candidates",
        "crystallize-status",
        "crystallize-draft",
        "crystallize-approve",
        "crystallize-promote",
        "crystallize-reject",
    ):
        assert op_id in ops, op_id
        assert ops[op_id].source == "lifecycle"
        assert ops[op_id].invocable is False
    assert ops["crystallize-promote"].readiness == "write_not_invocable"
    assert ops["crystallize-candidates"].readiness == "advisory_only"

    from greedy_token.capabilities import invoke_capability

    refused = invoke_capability(
        minimal_workspace, "crystallize-promote", log=False
    )
    assert refused.executed is False
    assert refused.refusal_code == "write_not_invocable"


# ---------------------------------------------------------------- CLI handlers


@allure.story("CLI")
@allure.title("cmd_crystallize_candidates: text + json with derived state")
def test_cmd_crystallize_candidates(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None, capsys
) -> None:
    code = cli.cmd_crystallize_candidates(_ns())
    out = capsys.readouterr().out
    assert code == 0
    assert "candidate" in out
    assert CRYSTAL_ID in out

    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    code = cli.cmd_crystallize_candidates(_ns(json=True))
    payload = json.loads(capsys.readouterr().out)
    entry = next(c for c in payload["crystals"] if c["crystal_id"] == CRYSTAL_ID)
    assert entry["state"] == "proposed"


@allure.story("CLI")
@allure.title("cmd_crystallize_status: text + json over the full funnel")
def test_cmd_crystallize_status(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None, capsys
) -> None:
    code = cli.cmd_crystallize_status(_ns(crystal_id=CRYSTAL_ID))
    out = capsys.readouterr().out
    assert code == 0
    assert "state: candidate" in out

    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace, actor="alice")
    code = cli.cmd_crystallize_status(_ns(crystal_id=CRYSTAL_ID, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "approved"
    assert payload["approved"]["actor"] == "alice"
    assert "promote" in payload["next"]


@allure.story("CLI")
@allure.title("cmd_crystallize_approve: text + json + refusal path")
def test_cmd_crystallize_approve(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None, capsys
) -> None:
    code = cli.cmd_crystallize_approve(_ns(crystal_id=CRYSTAL_ID))
    err = capsys.readouterr().err
    assert code == 1
    assert "crystallize approve:" in err

    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    code = cli.cmd_crystallize_approve(
        _ns(crystal_id=CRYSTAL_ID, by="alice", reason="lgtm")
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Approved" in out
    assert "alice" in out
    assert "lgtm" in out

    code = cli.cmd_crystallize_approve(_ns(crystal_id=CRYSTAL_ID, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "approved"


@allure.story("CLI")
@allure.title("cmd_crystallize_promote full path: draft → approve → applied + trust")
def test_cmd_crystallize_promote_full(
    minimal_workspace: Path, crystal_home: Path, no_cheap_llm: None, capsys
) -> None:
    l3.draft_crystal(CRYSTAL_ID, root=minimal_workspace)
    assert cli.cmd_crystallize_promote(_ns(crystal_id=CRYSTAL_ID)) == 1
    capsys.readouterr()

    l3.approve_crystal(CRYSTAL_ID, root=minimal_workspace)
    code = cli.cmd_crystallize_promote(_ns(crystal_id=CRYSTAL_ID, by="carol"))
    out = capsys.readouterr().out
    attach_text("promote", out)
    assert code == 0
    assert "approved → applied" in out
    assert "manifest:" in out

    code = cli.cmd_crystallize_promote(_ns(crystal_id=CRYSTAL_ID, json=True))
    err = capsys.readouterr().err
    assert code == 1
    assert "already applied" in err
