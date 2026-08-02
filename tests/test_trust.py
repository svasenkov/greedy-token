from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import allure
import pytest
import yaml

from greedy_token import trust as trust_mod
from greedy_token.cli import cmd_trust
from greedy_token.executors import RunPlan, execute_plan, execute_task, plan_run
from greedy_token.router import RouteDecision
from greedy_token.router import route_task
from greedy_token.subprocess_safe import (
    CommandInvocation,
    UnsafeCommandError,
    trusted_script_argv,
)
from greedy_token.trust import (
    APPROVAL_SOURCE_CLI,
    MANIFEST_MAX_BYTES,
    MANIFEST_VERSION,
    FileIdentity,
    TrustEntry,
    TrustManifestError,
    TrustVerificationError,
    approve_script,
    bind_verified_argv,
    list_trust_entries,
    normalize_manifest_path,
    revoke_script,
    trust_manifest_path,
    trusted_manifest_paths,
    verify_script,
    verify_trust_manifest,
)

pytestmark = [
    allure.epic("Security"),
    allure.parent_suite("Security"),
    allure.feature("Local trust manifest"),
    allure.suite("Local trust manifest"),
]


@pytest.fixture(autouse=True)
def isolated_trust_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    home = tmp_path.parent / f"{tmp_path.name}-greedy-token-home"
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    return home


def _script(root: Path, relative: str, content: str = "print('approved-ok')\n") -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _route(root: Path, relative: str, *, task: str = "run approved local check") -> None:
    command = (
        f"python {shlex.quote(relative)}"
        if relative.endswith(".py")
        else f"./{shlex.quote(relative)}"
    )
    (root / ".greedy-token.yaml").write_text(
        yaml.safe_dump(
            {
                "routes": [
                    {
                        "id": "approved-local",
                        "target": "python",
                        "read_only": True,
                        "patterns": [task],
                        "command": command,
                    }
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _run_cli(
    workspace: Path,
    trust_home: Path,
    *args: str,
    log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GREEDY_TOKEN_ROOT": str(workspace),
        "GREEDY_TOKEN_HOME": str(trust_home),
        "GREEDY_TOKEN_LOG": str(log or trust_home / "usage.jsonl"),
    }
    return subprocess.run(
        [sys.executable, "-m", "greedy_token", "--no-log", *args],
        capture_output=True,
        text=True,
        env=env,
    )


@allure.story("Approval lifecycle")
@allure.title("Add records content, identity, type, approval metadata, and optional note")
def test_approve_list_reapprove_and_revoke(
    minimal_workspace: Path,
) -> None:
    relative = "scripts/check trusted.py"
    _script(minimal_workspace, relative)

    entry = approve_script(minimal_workspace, relative, note="reviewed by Alice")
    manifest = trust_manifest_path(minimal_workspace)
    raw = json.loads(manifest.read_text(encoding="utf-8"))

    assert raw["version"] == MANIFEST_VERSION
    assert raw["scripts"] == [entry.to_dict()]
    assert entry.path == relative
    assert entry.script_type == "python"
    assert len(entry.sha256) == 64
    assert entry.approval_source == APPROVAL_SOURCE_CLI
    assert entry.approved_at.endswith("Z")
    assert entry.note == "reviewed by Alice"
    assert entry.file_identity.inode > 0
    assert trusted_manifest_paths(minimal_workspace) == frozenset({relative})
    assert list_trust_entries(minimal_workspace) == (entry,)
    assert not manifest.is_relative_to(minimal_workspace)
    if os.name == "posix":
        assert manifest.stat().st_mode & 0o777 == 0o600

    reapproved = approve_script(minimal_workspace, relative)
    assert reapproved.note == "reviewed by Alice"
    assert revoke_script(minimal_workspace, relative) is True
    assert revoke_script(minimal_workspace, relative) is False
    assert list_trust_entries(minimal_workspace) == ()


@allure.story("CLI")
@allure.title("trust add/list/verify/revoke handles spaces and Unicode without telemetry")
def test_cli_trust_lifecycle_spaces_unicode_and_no_telemetry(
    minimal_workspace: Path,
    isolated_trust_home: Path,
    tmp_path: Path,
) -> None:
    relative = "scripts/проверка с пробелом.py"
    secret = "DO_NOT_LOG_SCRIPT_SECRET"
    note = "human reviewed; no secret content copied"
    _script(minimal_workspace, relative, f"print('unicode-ok')\n# {secret}\n")
    _route(minimal_workspace, relative, task="execute Unicode approved script")
    telemetry = tmp_path / "trust-usage.jsonl"

    added = _run_cli(
        minimal_workspace,
        isolated_trust_home,
        "trust",
        "add",
        relative,
        "--note",
        note,
        log=telemetry,
    )
    assert added.returncode == 0
    assert relative in added.stdout
    assert secret not in added.stdout + added.stderr

    listed = _run_cli(
        minimal_workspace,
        isolated_trust_home,
        "trust",
        "list",
        log=telemetry,
    )
    assert listed.returncode == 0
    assert relative in listed.stdout
    assert note in listed.stdout
    assert secret not in listed.stdout + listed.stderr

    verified = _run_cli(
        minimal_workspace,
        isolated_trust_home,
        "trust",
        "verify",
        log=telemetry,
    )
    assert verified.returncode == 0
    assert f"OK   {relative}" in verified.stdout

    executed = _run_cli(
        minimal_workspace,
        isolated_trust_home,
        "run",
        "execute Unicode approved script",
        "--execute",
        log=telemetry,
    )
    assert executed.returncode == 0
    assert "unicode-ok" in executed.stdout
    assert secret not in executed.stdout + executed.stderr

    revoked = _run_cli(
        minimal_workspace,
        isolated_trust_home,
        "trust",
        "revoke",
        relative,
        log=telemetry,
    )
    assert revoked.returncode == 0
    assert not telemetry.exists()

    blocked = _run_cli(
        minimal_workspace,
        isolated_trust_home,
        "run",
        "execute Unicode approved script",
        "--execute",
        log=telemetry,
    )
    assert blocked.returncode == 1
    assert "not registered or approved" in blocked.stdout


@allure.story("Execution-time verification")
@allure.title("Script modification after planning blocks launch until re-approval")
def test_modified_script_is_rechecked_immediately_before_execution(
    minimal_workspace: Path,
) -> None:
    relative = "scripts/approved-check.py"
    script = _script(minimal_workspace, relative)
    _route(minimal_workspace, relative)
    approve_script(minimal_workspace, relative)

    decision = route_task("run approved local check", minimal_workspace)
    plan = plan_run(decision, "run approved local check", minimal_workspace)
    assert plan.executable is True
    assert plan.authorization == f"manifest:{relative}"

    side_effect = minimal_workspace / "SIDE_EFFECT"
    script.write_text(
        "from pathlib import Path\nPath('SIDE_EFFECT').write_text('owned')\n",
        encoding="utf-8",
    )
    with patch("greedy_token.executors.subprocess.run") as run:
        code, output = execute_plan(plan)
    assert code == 1
    assert "SHA-256 mismatch" in output
    run.assert_not_called()
    assert not side_effect.exists()

    approve_script(minimal_workspace, relative, note="reviewed changed content")
    result = execute_task("run approved local check", minimal_workspace)
    assert result.exit_code == 0
    assert side_effect.read_text(encoding="utf-8") == "owned"


@allure.story("Execution-time verification")
@allure.title("Wrong manifest hash blocks verification and execution")
def test_wrong_hash_is_rejected(minimal_workspace: Path) -> None:
    relative = "scripts/wrong-hash.py"
    _script(minimal_workspace, relative)
    _route(minimal_workspace, relative)
    approve_script(minimal_workspace, relative)
    manifest = trust_manifest_path(minimal_workspace)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["scripts"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(TrustVerificationError, match="SHA-256 mismatch"):
        verify_script(minimal_workspace, relative)
    result = execute_task("run approved local check", minimal_workspace)
    assert result.exit_code == 1
    assert "SHA-256 mismatch" in result.output


@allure.story("Path replacement")
@allure.title("Final-component symlink swap is rejected even when target has approved bytes")
def test_symlink_swap_is_rejected(minimal_workspace: Path, tmp_path: Path) -> None:
    relative = "scripts/symlink-swap.py"
    approved_bytes = "print('approved-ok')\n"
    script = _script(minimal_workspace, relative, approved_bytes)
    approve_script(minimal_workspace, relative)
    outside = tmp_path / "outside-same.py"
    outside.write_text(approved_bytes, encoding="utf-8")
    script.unlink()
    script.symlink_to(outside)

    with pytest.raises(TrustVerificationError, match="symlink|replaced"):
        verify_script(minimal_workspace, relative)


@allure.story("Path replacement")
@allure.title("Parent-directory symlink swap cannot redirect an approved path")
def test_parent_symlink_swap_is_rejected(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    relative = "scripts/nested/check.py"
    approved_bytes = "print('approved-ok')\n"
    _script(minimal_workspace, relative, approved_bytes)
    approve_script(minimal_workspace, relative)

    original = minimal_workspace / "scripts" / "nested"
    moved = minimal_workspace / "scripts" / "nested-approved"
    original.rename(moved)
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "check.py").write_text(approved_bytes, encoding="utf-8")
    original.symlink_to(outside, target_is_directory=True)

    with pytest.raises(TrustVerificationError, match="symlink|replaced"):
        verify_script(minimal_workspace, relative)


@allure.story("Path replacement")
@allure.title("Deleted and recreated same-content file still needs re-approval")
def test_deleted_recreated_same_content_is_rejected(minimal_workspace: Path) -> None:
    relative = "scripts/recreated.py"
    content = "print('same-content')\n"
    script = _script(minimal_workspace, relative, content)
    approved = approve_script(minimal_workspace, relative)
    held_inode = script.with_name("held-original.py")
    os.link(script, held_inode)
    script.unlink()
    script.write_text(content, encoding="utf-8")
    assert FileIdentity.from_stat(script.stat()) != approved.file_identity

    with pytest.raises(TrustVerificationError, match="file identity changed"):
        verify_script(minimal_workspace, relative)


@allure.story("Manifest confinement")
@allure.title("Absolute, outside-workspace, Windows, and unsupported paths are rejected")
@pytest.mark.parametrize(
    "path",
    [
        "/tmp/evil.py",
        "../evil.py",
        "scripts/../../evil.py",
        r"C:\temp\evil.py",
        r"scripts\evil.py",
        ".",
        "",
        "scripts/not-executable.txt",
    ],
)
def test_manifest_rejects_unsafe_paths(
    minimal_workspace: Path,
    path: str,
) -> None:
    with pytest.raises(TrustManifestError):
        approve_script(minimal_workspace, path)


@allure.story("Manifest confinement")
@allure.title("Approval rejects a workspace symlink that escapes the root")
def test_approval_rejects_symlink_escape(
    minimal_workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    link = minimal_workspace / "scripts" / "escape.py"
    link.symlink_to(outside)
    with pytest.raises(TrustVerificationError, match="symlink|replaced"):
        approve_script(minimal_workspace, "scripts/escape.py")


@allure.story("Safe migration")
@allure.title("Bare trusted_script_paths is a deprecated refusal, never authority")
def test_bare_trusted_paths_do_not_authorize_argv(minimal_workspace: Path) -> None:
    relative = "scripts/legacy.py"
    _script(minimal_workspace, relative)
    with pytest.raises(UnsafeCommandError, match="deprecated and dry-run only"):
        trusted_script_argv(
            ("python", relative),
            cwd=minimal_workspace,
            root=minimal_workspace,
            trusted_script_paths=(relative,),
        )


@allure.story("Preset isolation")
@allure.title("Malicious file preset cannot copy trust fields or execute its script")
def test_malicious_file_preset_cannot_add_trust(minimal_workspace: Path) -> None:
    from greedy_token.paths import load_route_preset, upsert_workspace_routes

    relative = "scripts/preset-evil.py"
    side_effect = minimal_workspace / "PRESET_SIDE_EFFECT"
    _script(
        minimal_workspace,
        relative,
        "from pathlib import Path\nPath('PRESET_SIDE_EFFECT').write_text('owned')\n",
    )
    preset = minimal_workspace / "malicious-routes.yaml"
    preset.write_text(
        yaml.safe_dump(
            {
                "trusted_script_paths": [relative],
                "trust_manifest": {
                    "version": 1,
                    "scripts": [{"path": relative, "sha256": "0" * 64}],
                },
                "routes": [
                    {
                        "id": "preset-evil",
                        "target": "python",
                        "read_only": True,
                        "patterns": ["malicious preset route"],
                        "command": f"python {relative}",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    overlay = load_route_preset(str(preset))
    upsert_workspace_routes(minimal_workspace, overlay)
    config = yaml.safe_load(
        (minimal_workspace / ".greedy-token.yaml").read_text(encoding="utf-8")
    )
    assert "trusted_script_paths" not in config
    assert "trust_manifest" not in config
    assert list_trust_entries(minimal_workspace) == ()

    result = execute_task("malicious preset route", minimal_workspace)
    assert result.exit_code == 1
    assert not side_effect.exists()


@allure.story("Manifest integrity")
@allure.title("Malformed, absolute-path, duplicate, and symlink manifests fail closed")
def test_malformed_manifests_fail_closed(
    minimal_workspace: Path,
    tmp_path: Path,
) -> None:
    relative = "scripts/check.py"
    _script(minimal_workspace, relative)
    entry = approve_script(minimal_workspace, relative)
    manifest = trust_manifest_path(minimal_workspace)

    malformed_payloads = [
        ([], "trust manifest must contain exactly version and scripts"),
        (
            {"version": MANIFEST_VERSION, "scripts": [], "unexpected": True},
            "trust manifest must contain exactly version and scripts",
        ),
        (
            {"version": MANIFEST_VERSION, "scripts": "not-a-list"},
            "trust manifest scripts must be a list",
        ),
        (
            {"version": 999, "scripts": []},
            "unsupported trust manifest version: 999",
        ),
        (
            {
                "version": MANIFEST_VERSION,
                "scripts": [{**entry.to_dict(), "path": "/tmp/outside.py"}],
            },
            "absolute script paths are not allowed in trust manifests",
        ),
        (
            {
                "version": MANIFEST_VERSION,
                "scripts": [entry.to_dict(), entry.to_dict()],
            },
            "trust manifest contains duplicate script paths",
        ),
    ]
    for payload, message in malformed_payloads:
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(TrustManifestError) as raised:
            list_trust_entries(minimal_workspace)
        assert str(raised.value) == message

    outside_manifest = tmp_path / "outside-manifest.json"
    outside_manifest.write_text(
        json.dumps({"version": MANIFEST_VERSION, "scripts": []}),
        encoding="utf-8",
    )
    manifest.unlink()
    manifest.symlink_to(outside_manifest)
    with pytest.raises(TrustManifestError, match="must not be a symlink"):
        list_trust_entries(minimal_workspace)


@allure.story("Verification report")
@allure.title("verify reports valid and stale entries without exposing script content")
def test_verify_manifest_reports_each_entry(minimal_workspace: Path) -> None:
    good_rel = "scripts/good.py"
    bad_rel = "scripts/bad.py"
    _script(minimal_workspace, good_rel, "print('GOOD_SECRET')\n")
    bad = _script(minimal_workspace, bad_rel, "print('BAD_SECRET')\n")
    approve_script(minimal_workspace, good_rel)
    approve_script(minimal_workspace, bad_rel)
    bad.write_text("print('MODIFIED_SECRET')\n", encoding="utf-8")

    checks = verify_trust_manifest(minimal_workspace)
    by_path = {check.entry.path: check for check in checks}
    assert by_path[good_rel].ok is True
    assert by_path[good_rel].error == ""
    assert by_path[bad_rel].ok is False
    assert "SHA-256 mismatch" in by_path[bad_rel].error
    assert "GOOD_SECRET" not in repr(checks)
    assert "MODIFIED_SECRET" not in repr(checks)


@allure.story("Descriptor binding")
@allure.title("Verified Python and shell argv bind to FD; fallback keeps structured argv")
def test_bind_verified_argv_branches(
    minimal_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    py_rel = "scripts/bind.py"
    sh_rel = "scripts/bind.sh"
    _script(minimal_workspace, py_rel)
    shell = _script(minimal_workspace, sh_rel, "#!/bin/sh\necho shell-ok\n")
    shell.chmod(0o755)
    approve_script(minimal_workspace, py_rel)
    approve_script(minimal_workspace, sh_rel)

    with verify_script(minimal_workspace, py_rel) as verified:
        monkeypatch.setattr("greedy_token.trust._fd_execution_supported", lambda: True)
        argv, pass_fds = bind_verified_argv(
            verified, ("python", py_rel, "--flag")
        )
        assert argv == ["python", f"/dev/fd/{verified.fd}", "--flag"]
        assert pass_fds == (verified.fd,)

    with verify_script(minimal_workspace, sh_rel) as verified:
        argv, pass_fds = bind_verified_argv(verified, (sh_rel, "arg"))
        assert argv == [f"/dev/fd/{verified.fd}", "arg"]
        assert pass_fds == (verified.fd,)

    with verify_script(minimal_workspace, py_rel) as verified:
        monkeypatch.setattr("greedy_token.trust._fd_execution_supported", lambda: False)
        argv, pass_fds = bind_verified_argv(verified, ("python", py_rel))
        assert argv == ["python", py_rel]
        assert pass_fds == ()


@allure.story("CLI handler")
@allure.title("Trust CLI handler covers empty, stale, missing, and invalid operations")
def test_cmd_trust_handler_edges(
    minimal_workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cmd_trust(Namespace(trust_action="list")) == 0
    assert "No approved" in capsys.readouterr().out
    assert cmd_trust(Namespace(trust_action="verify")) == 0
    assert "empty" in capsys.readouterr().out
    assert cmd_trust(Namespace(trust_action="revoke", path="scripts/missing.py")) == 1
    assert "Not approved" in capsys.readouterr().err
    assert (
        cmd_trust(
            Namespace(
                trust_action="add",
                path="../outside.py",
                note=None,
            )
        )
        == 1
    )
    assert "trust add" in capsys.readouterr().err


@allure.story("CLI handler")
@allure.title("Trust CLI handler renders successful add/list/verify/revoke and stale verify")
def test_cmd_trust_handler_success_and_stale(
    minimal_workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    relative = "scripts/handler.py"
    script = _script(minimal_workspace, relative)
    add_args = Namespace(
        trust_action="add",
        path=relative,
        note="handler review",
    )
    assert cmd_trust(add_args) == 0
    assert "Approved" in capsys.readouterr().out

    _script(minimal_workspace, "scripts/no-note.py")
    assert approve_script(minimal_workspace, "scripts/no-note.py", note="").note == ""
    assert cmd_trust(Namespace(trust_action="list")) == 0
    listing = capsys.readouterr().out
    assert relative in listing
    assert "handler review" in listing

    assert cmd_trust(Namespace(trust_action="verify")) == 0
    assert f"OK   {relative}" in capsys.readouterr().out

    script.write_text("print('changed')\n", encoding="utf-8")
    assert cmd_trust(Namespace(trust_action="verify")) == 1
    assert f"FAIL {relative}" in capsys.readouterr().err

    assert cmd_trust(Namespace(trust_action="revoke", path=relative)) == 0
    assert f"Revoked {relative}" in capsys.readouterr().out


@allure.story("Manifest schema")
@allure.title("File identity schema rejects wrong shape and every invalid integer form")
@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"device": 1, "inode": 2, "extra": 3},
        {"device": "1", "inode": 2},
        {"device": True, "inode": 2},
        {"device": -1, "inode": 2},
        {"device": 1, "inode": "2"},
        {"device": 1, "inode": False},
        {"device": 1, "inode": -2},
    ],
)
def test_file_identity_rejects_invalid_schema(value: object) -> None:
    with pytest.raises(TrustManifestError):
        FileIdentity.from_dict(value)


@allure.story("Manifest schema")
@allure.title("Trust entry parser rejects malformed metadata fields")
def test_trust_entry_rejects_malformed_fields(minimal_workspace: Path) -> None:
    relative = "scripts/schema.py"
    _script(minimal_workspace, relative)
    base = approve_script(minimal_workspace, relative).to_dict()

    malformed: list[object] = [
        "not-an-object",
        {key: value for key, value in base.items() if key != "path"},
        {**base, "unexpected": True},
        {**base, "path": 123},
        {**base, "path": "./scripts/schema.py"},
        {**base, "sha256": 123},
        {**base, "sha256": "ABC"},
        {**base, "script_type": "ruby"},
        {**base, "script_type": "shell"},
        {**base, "approved_at": 123},
        {**base, "approved_at": ""},
        {**base, "approved_at": "not-a-time"},
        {**base, "approved_at": "2026-07-31T12:00:00"},
        {**base, "approval_source": 123},
        {**base, "approval_source": "   "},
        {**base, "note": 123},
    ]
    for payload in malformed:
        with pytest.raises(TrustManifestError):
            TrustEntry.from_dict(payload)


@allure.story("Manifest schema")
@allure.title("Path normalizer rejects every outside-root spelling with stable diagnostics")
@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("", "script path must be non-empty and contain no NUL"),
        ("scripts/a\x00b.py", "script path must be non-empty and contain no NUL"),
        (
            "/tmp/evil.py",
            "absolute script paths are not allowed in trust manifests",
        ),
        (
            r"C:\temp\evil.py",
            "absolute script paths are not allowed in trust manifests",
        ),
        (
            r"C:relative.py",
            "absolute script paths are not allowed in trust manifests",
        ),
        (r"scripts\evil.py", "trust manifest paths must use '/' separators"),
        (".", "trust manifest path must stay inside the workspace"),
        ("../evil.py", "trust manifest path must stay inside the workspace"),
        (
            "scripts/../../evil.py",
            "trust manifest path must stay inside the workspace",
        ),
    ],
)
def test_path_normalizer_additional_rejections(path: str, message: str) -> None:
    with pytest.raises(TrustManifestError) as raised:
        normalize_manifest_path(path)
    assert str(raised.value) == message


@allure.story("Portable verification")
@allure.title("Portable fallback verifies regular files and rejects symlink, missing, and directory")
def test_portable_open_fallback_branches(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "scripts/portable.py"
    _script(minimal_workspace, relative)
    monkeypatch.setattr(trust_mod, "_secure_dir_fd_supported", lambda: False)
    approve_script(minimal_workspace, relative)
    with verify_script(minimal_workspace, relative):
        pass

    missing = "scripts/missing-portable.py"
    with pytest.raises(TrustVerificationError, match="missing"):
        trust_mod._open_script(minimal_workspace, missing)

    symlink = minimal_workspace / "scripts" / "portable-link.py"
    symlink.symlink_to(minimal_workspace / relative)
    with pytest.raises(TrustVerificationError, match="symlink"):
        trust_mod._open_script(minimal_workspace, "scripts/portable-link.py")

    directory = minimal_workspace / "scripts" / "directory.py"
    directory.mkdir()
    with pytest.raises(TrustVerificationError, match="changed while"):
        trust_mod._open_script(minimal_workspace, "scripts/directory.py")


@allure.story("Portable verification")
@allure.title("Portable fallback detects identity race and closes descriptors on open race")
def test_portable_open_identity_and_open_race(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "scripts/portable-race.py"
    script = _script(minimal_workspace, relative)
    other = _script(minimal_workspace, "scripts/other-race.py")
    real_fstat = os.fstat
    monkeypatch.setattr(trust_mod.os, "fstat", lambda _fd: other.stat())
    with pytest.raises(TrustVerificationError, match="changed while"):
        trust_mod._open_portable_nofollow(minimal_workspace, relative)
    monkeypatch.setattr(trust_mod.os, "fstat", real_fstat)

    real_lstat = Path.lstat
    calls = 0

    def fail_second_lstat(self: Path):
        nonlocal calls
        if self == script:
            calls += 1
            if calls == 2:
                raise OSError("path replaced")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", fail_second_lstat)
    with pytest.raises(TrustVerificationError, match="replaced"):
        trust_mod._open_portable_nofollow(minimal_workspace, relative)

    calls = 0

    def fail_second_lstat_with_trust_error(self: Path):
        nonlocal calls
        if self == script:
            calls += 1
            if calls == 2:
                raise TrustVerificationError("identity raced")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", fail_second_lstat_with_trust_error)
    with pytest.raises(TrustVerificationError, match="identity raced"):
        trust_mod._open_portable_nofollow(minimal_workspace, relative)

    closed: list[int] = []
    monkeypatch.setattr(trust_mod.os, "open", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(trust_mod.os, "close", closed.append)

    calls = 0
    monkeypatch.setattr(Path, "lstat", fail_second_lstat_with_trust_error)
    with pytest.raises(TrustVerificationError, match="identity raced"):
        trust_mod._open_portable_nofollow(minimal_workspace, relative)
    assert closed == [0]

    calls = 0
    closed.clear()
    monkeypatch.setattr(Path, "lstat", fail_second_lstat)
    with pytest.raises(TrustVerificationError, match="replaced"):
        trust_mod._open_portable_nofollow(minimal_workspace, relative)
    assert closed == [0]

    def fail_before_open(self: Path):
        if self == minimal_workspace / "scripts":
            raise TrustVerificationError("early rejection")
        return real_lstat(self)

    closed.clear()
    monkeypatch.setattr(Path, "lstat", fail_before_open)
    with pytest.raises(TrustVerificationError, match="early rejection"):
        trust_mod._open_portable_nofollow(minimal_workspace, relative)
    assert closed == []


@allure.story("POSIX verification")
@allure.title("POSIX opener covers no-CLOEXEC and non-regular final component")
@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor contract")
def test_posix_open_without_cloexec_and_directory_rejection(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "scripts/no-cloexec.py"
    _script(minimal_workspace, relative)
    monkeypatch.delattr(trust_mod.os, "O_CLOEXEC", raising=False)
    fd, _file_stat = trust_mod._open_posix_nofollow(minimal_workspace, relative)
    os.close(fd)

    directory = minimal_workspace / "scripts" / "not-regular.py"
    directory.mkdir()
    with pytest.raises(TrustVerificationError, match="not a regular file"):
        trust_mod._open_posix_nofollow(
            minimal_workspace, "scripts/not-regular.py"
        )


@allure.story("POSIX verification")
@allure.title("POSIX opener uses directory, no-follow, and close-on-exec flags")
@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor contract")
def test_posix_open_uses_all_security_flags(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "scripts/flags.py"
    _script(minimal_workspace, relative)
    real_open = os.open
    calls: list[int] = []

    def capture_open(path: object, flags: int, *, dir_fd: int | None = None) -> int:
        calls.append(flags)
        if dir_fd is None:
            return real_open(path, flags)
        return real_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(trust_mod.os, "open", capture_open)
    fd, _file_stat = trust_mod._open_posix_nofollow(minimal_workspace, relative)
    os.close(fd)

    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | cloexec
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | cloexec
    assert calls == [directory_flags, directory_flags, file_flags]


@allure.story("Manifest IO")
@allure.title("Oversized, invalid JSON, write-symlink, and atomic-write errors fail closed")
def test_manifest_io_failures(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = trust_manifest_path(minimal_workspace)
    manifest.parent.mkdir(parents=True)
    exact_limit = json.dumps({"version": MANIFEST_VERSION, "scripts": []})
    manifest.write_text(
        exact_limit + (" " * (MANIFEST_MAX_BYTES - len(exact_limit))),
        encoding="utf-8",
    )
    assert list_trust_entries(minimal_workspace) == ()

    manifest.write_text(" " * (MANIFEST_MAX_BYTES + 1), encoding="utf-8")
    with pytest.raises(TrustManifestError) as raised:
        list_trust_entries(minimal_workspace)
    assert str(raised.value) == "trust manifest exceeds 1 MiB"

    manifest.write_text("{invalid", encoding="utf-8")
    with pytest.raises(TrustManifestError, match="cannot read"):
        list_trust_entries(minimal_workspace)

    manifest.unlink()
    target = manifest.with_name("target.json")
    target.write_text("{}", encoding="utf-8")
    manifest.symlink_to(target)
    with patch("greedy_token.trust._read_entries", return_value=()):
        with pytest.raises(TrustManifestError, match="must not be a symlink"):
            trust_mod._write_entries(minimal_workspace, ())

    manifest.unlink()
    with patch("greedy_token.trust.os.replace", side_effect=OSError("replace failed")):
        with pytest.raises(OSError, match="replace failed"):
            trust_mod._write_entries(minimal_workspace, ())
    assert not list(manifest.parent.glob(".manifest.*.tmp"))


@allure.story("Verification edges")
@allure.title("Invalid root, empty source, missing approval, and type drift fail closed")
def test_verification_edge_refusals(
    minimal_workspace: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(TrustVerificationError, match="root is not a directory"):
        trust_mod._open_script(tmp_path / "missing-root", "scripts/check.py")

    relative = "scripts/edge.py"
    script = _script(minimal_workspace, relative)
    with pytest.raises(TrustManifestError) as raised:
        approve_script(minimal_workspace, relative, approval_source=" ")
    assert str(raised.value) == "approval source must be non-empty"
    with pytest.raises(TrustVerificationError, match="not approved"):
        verify_script(minimal_workspace, relative)

    approved = approve_script(minimal_workspace, relative)
    wrong_type = TrustEntry(
        path=approved.path,
        sha256=approved.sha256,
        script_type="shell",
        approved_at=approved.approved_at,
        approval_source=approved.approval_source,
        file_identity=approved.file_identity,
    )
    with patch("greedy_token.trust._read_entries", return_value=(wrong_type,)):
        with pytest.raises(TrustVerificationError, match="script type changed"):
            verify_script(minimal_workspace, relative)

    verified = verify_script(minimal_workspace, relative)
    verified.close()
    verified.close()
    assert verified.fd == -1
    assert script.is_file()


@allure.story("Descriptor binding")
@allure.title("Descriptor binding rejects malformed Python and shell argv")
def test_bind_verified_argv_rejects_missing_script_argv(
    minimal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "scripts/bind-errors.py"
    _script(minimal_workspace, relative)
    approve_script(minimal_workspace, relative)
    monkeypatch.setattr(trust_mod, "_fd_execution_supported", lambda: True)
    with verify_script(minimal_workspace, relative) as verified:
        with pytest.raises(TrustVerificationError) as raised:
            bind_verified_argv(verified, ("python",))
        assert str(raised.value) == "verified Python invocation has no script argv"
        shell_verified = trust_mod.VerifiedScript(
            entry=TrustEntry(
                path="scripts/bind-errors.sh",
                sha256=verified.entry.sha256,
                script_type="shell",
                approved_at=verified.entry.approved_at,
                approval_source=verified.entry.approval_source,
                file_identity=verified.entry.file_identity,
            ),
            fd=verified.fd,
        )
        with pytest.raises(TrustVerificationError) as raised:
            bind_verified_argv(shell_verified, ())
        assert str(raised.value) == "verified shell invocation has empty argv"


@allure.story("Execution plan hardening")
@allure.title("Manifest plans require complete and unchanged script metadata")
@pytest.mark.parametrize(
    ("script_path", "script_type", "authorization", "message"),
    [
        (
            "",
            "python",
            "manifest:scripts/plan.py",
            "manifest-authorised plan is missing script metadata",
        ),
        (
            "scripts/plan.py",
            "",
            "manifest:scripts/plan.py",
            "manifest-authorised plan is missing script metadata",
        ),
        (
            "scripts/plan.py",
            "python",
            "manifest:scripts/forged.py",
            "manifest-authorised argv changed after planning",
        ),
        (
            "scripts/plan.py",
            "shell",
            "manifest:scripts/plan.py",
            "manifest-authorised argv changed after planning",
        ),
    ],
)
def test_manifest_plan_metadata_is_revalidated(
    minimal_workspace: Path,
    script_path: str,
    script_type: str,
    authorization: str,
    message: str,
) -> None:
    relative = "scripts/plan.py"
    _script(minimal_workspace, relative)
    plan = RunPlan(
        decision=RouteDecision(
            target="python",
            route_id="manifest-plan",
            confidence=1.0,
            matched=[],
            command=f"python {relative}",
            note="",
            domains=[],
            read_only=True,
        ),
        command=f"python {relative}",
        dry_run_output=f"python {relative}",
        executable=True,
        argv=("python", relative),
        cwd=minimal_workspace,
        authorization=authorization,
        script_path=script_path,
        script_type=script_type,
    )
    code, output = execute_plan(plan)
    assert code == 1
    assert output == f"Refusing --execute: trust verification failed: {message}"


@allure.story("Execution plan hardening")
@allure.title("Each missing structured invocation field independently blocks execution")
@pytest.mark.parametrize("missing", ["argv", "cwd", "authorization"])
def test_execute_plan_requires_each_structured_field(
    minimal_workspace: Path,
    missing: str,
) -> None:
    relative = "scripts/structured.py"
    _script(minimal_workspace, relative)
    plan = RunPlan(
        decision=RouteDecision(
            target="python",
            route_id="structured-plan",
            confidence=1.0,
            matched=[],
            command=f"python {relative}",
            note="",
            domains=[],
            read_only=True,
        ),
        command=f"python {relative}",
        dry_run_output="structured dry-run",
        executable=True,
        argv=None if missing == "argv" else ("python", relative),
        cwd=None if missing == "cwd" else minimal_workspace,
        authorization="" if missing == "authorization" else f"wrapper:{relative}",
    )
    with patch("greedy_token.executors.subprocess.run") as run:
        code, output = execute_plan(plan)
    assert code == 1
    assert output == (
        "Refusing --execute: structured trusted argv is missing.\n"
        "Dry-run:\nstructured dry-run"
    )
    run.assert_not_called()


@allure.story("Execution plan hardening")
@allure.title("Any single revalidated manifest metadata drift blocks before file verification")
@pytest.mark.parametrize(
    ("authorization", "script_path", "script_type"),
    [
        ("manifest:scripts/other.py", "scripts/plan.py", "python"),
        ("manifest:scripts/plan.py", "scripts/other.py", "python"),
        ("manifest:scripts/plan.py", "scripts/plan.py", "shell"),
    ],
)
def test_execute_plan_rejects_each_revalidated_metadata_drift(
    minimal_workspace: Path,
    authorization: str,
    script_path: str,
    script_type: str,
) -> None:
    relative = "scripts/plan.py"
    plan = RunPlan(
        decision=RouteDecision(
            target="python",
            route_id="manifest-drift",
            confidence=1.0,
            matched=[],
            command=f"python {relative}",
            note="",
            domains=[],
            read_only=True,
        ),
        command=f"python {relative}",
        dry_run_output=f"python {relative}",
        executable=True,
        argv=("python", relative),
        cwd=minimal_workspace,
        authorization=f"manifest:{relative}",
        script_path=relative,
        script_type="python",
    )
    revalidated = CommandInvocation(
        cwd=minimal_workspace,
        argv=("python", relative),
        authorization=authorization,
        script_path=script_path,
        script_type=script_type,
    )
    with (
        patch("greedy_token.executors.trusted_script_argv", return_value=revalidated),
        patch("greedy_token.executors.verify_script") as verify,
    ):
        code, output = execute_plan(plan)
    assert code == 1
    assert output == (
        "Refusing --execute: trust verification failed: "
        "manifest-authorised argv changed after planning"
    )
    verify.assert_not_called()


@allure.story("Structured argv")
@allure.title("Interpreter and direct execution must match the approved script type")
def test_trusted_argv_rejects_type_mismatches(minimal_workspace: Path) -> None:
    py_rel = "scripts/type.py"
    sh_rel = "scripts/type.sh"
    _script(minimal_workspace, py_rel)
    _script(minimal_workspace, sh_rel, "#!/bin/sh\n")
    with pytest.raises(UnsafeCommandError, match="python trusted scripts"):
        trusted_script_argv(
            ("python", sh_rel),
            cwd=minimal_workspace,
            root=minimal_workspace,
            manifest_script_paths=(sh_rel,),
        )
    with pytest.raises(UnsafeCommandError, match="direct trusted scripts"):
        trusted_script_argv(
            (py_rel,),
            cwd=minimal_workspace,
            root=minimal_workspace,
            manifest_script_paths=(py_rel,),
        )


@allure.story("Structured argv")
@allure.title("Code-string guards and trust refusals keep exact fail-closed diagnostics")
def test_trusted_argv_security_refusals_are_exact(minimal_workspace: Path) -> None:
    py_rel = "scripts/errors.py"
    sh_rel = "scripts/errors.sh"
    txt_rel = "scripts/errors.txt"
    _script(minimal_workspace, py_rel)
    _script(minimal_workspace, sh_rel, "#!/bin/sh\n")
    _script(minimal_workspace, txt_rel, "not executable\n")

    def assert_unsafe(
        argv: tuple[str, ...],
        message: str,
        *,
        command_cwd: Path | None = None,
        **paths: tuple[str, ...],
    ) -> None:
        with pytest.raises(UnsafeCommandError) as raised:
            trusted_script_argv(
                argv,
                cwd=command_cwd or minimal_workspace,
                root=minimal_workspace,
                **paths,
            )
        assert str(raised.value) == message

    assert_unsafe(("python", "-c", "print(1)"), "python -c is not allowed")
    assert_unsafe(("python", "-Ic", "print(1)"), "python -c is not allowed")
    assert_unsafe(("sh", "-c", "echo owned"), "shell -c is not allowed")
    assert_unsafe(("bash", "-xc", "echo owned"), "shell -c is not allowed")
    assert_unsafe(
        ("python", py_rel),
        "script cwd must equal the workspace root",
        manifest_script_paths=(py_rel,),
        command_cwd=(minimal_workspace / "scripts"),
    )
    assert_unsafe((), "empty script argv")
    assert_unsafe(
        ("python", "-x"),
        "python commands must be 'python <trusted-script.py> [args...]'",
    )
    assert_unsafe(
        ("python", sh_rel),
        "python trusted scripts must end in .py",
        manifest_script_paths=(sh_rel,),
    )
    assert_unsafe(
        ("sh", sh_rel),
        "shell interpreters are not route executors; register the script path",
        manifest_script_paths=(sh_rel,),
    )
    assert_unsafe(
        (py_rel,),
        "direct trusted scripts must end in .sh",
        manifest_script_paths=(py_rel,),
    )
    assert_unsafe(
        ("python", "/tmp/outside.py"),
        "absolute script paths are not allowed",
    )
    assert_unsafe(
        ("python", txt_rel),
        "trusted script must end in .py or .sh",
    )

    missing = "scripts/missing.py"
    with pytest.raises(FileNotFoundError) as raised:
        trusted_script_argv(
            ("python", missing),
            cwd=minimal_workspace,
            root=minimal_workspace,
        )
    assert str(raised.value) == f"Script not found: {minimal_workspace / missing}"

    fake_python = _script(minimal_workspace, "bin/python")
    assert_unsafe(
        (str(fake_python), py_rel),
        "absolute Python executable is not registered",
        manifest_script_paths=(py_rel,),
    )
    assert_unsafe(
        ("python", py_rel),
        (
            "trusted_script_paths is deprecated and dry-run only; "
            f"review and approve {py_rel!r} with 'greedy-token trust add'"
        ),
        trusted_script_paths=(py_rel,),
    )
    assert_unsafe(
        ("python", py_rel),
        (
            "script is not registered or approved in the local trust manifest: "
            f"{py_rel!r}"
        ),
    )


@allure.story("Structured argv")
@allure.title("Shell trust metadata and every script argument survive validation")
def test_trusted_shell_argv_preserves_type_and_validates_first_argument(
    minimal_workspace: Path,
) -> None:
    relative = "scripts/direct.sh"
    _script(minimal_workspace, relative, "#!/bin/sh\n")
    invocation = trusted_script_argv(
        (f"./{relative}", "--flag", "value"),
        cwd=minimal_workspace,
        root=minimal_workspace,
        manifest_script_paths=(f"./{relative}",),
    )
    assert invocation.argv == (f"./{relative}", "--flag", "value")
    assert invocation.authorization == f"manifest:{relative}"
    assert invocation.script_path == relative
    assert invocation.script_type == "shell"

    with pytest.raises(UnsafeCommandError) as raised:
        trusted_script_argv(
            (relative, "/tmp/first-arg"),
            cwd=minimal_workspace,
            root=minimal_workspace,
            manifest_script_paths=(relative,),
        )
    assert str(raised.value) == "absolute argument path is not allowed: '/tmp/first-arg'"


@allure.story("Threat model")
@allure.title("TOCTOU limitations and platform guarantees are documented")
def test_toctou_threat_model_is_documented() -> None:
    text = Path("docs/trust-manifest.md").read_text(encoding="utf-8")
    assert "TOCTOU" in text
    assert "/dev/fd" in text
    assert "Windows" in text
    assert "same-user" in text
    assert "trusted_script_paths" in text
