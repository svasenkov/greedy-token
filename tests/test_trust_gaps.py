"""Mutation kill-tests for trust.py (manifest parsing, fd verification)."""
from __future__ import annotations

import os
import stat as stat_mod
import tempfile
from pathlib import Path

import pytest

import allure
import greedy_token.trust as trust
from greedy_token.trust import (
    FileIdentity,
    TrustEntry,
    TrustManifestError,
    TrustVerificationError,
    VerifiedScript,
)


def _entry(**over) -> TrustEntry:
    base = dict(
        path="scripts/x.py",
        sha256="a" * 64,
        script_type="python",
        approved_at="2024-01-01T00:00:00Z",
        approval_source="local-cli",
        file_identity=FileIdentity(device=1, inode=2),
        note="",
    )
    base.update(over)
    return TrustEntry(**base)


def _entry_dict(**over) -> dict:
    d = _entry().to_dict()
    d.update(over)
    return d


# --- TrustVerificationError / VerifiedScript ---


@allure.title("TrustVerificationError: default code is exactly 'internal'")
def test_verification_error_default_code() -> None:
    err = TrustVerificationError("boom")
    assert err.code == "internal"  # kills XXinternalXX / INTERNAL
    assert str(err) == "boom"


@allure.title("VerifiedScript.close: fd 0 is still closed exactly once")
def test_verified_script_close_zero_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[int] = []
    monkeypatch.setattr(os, "close", closed.append)
    vs = VerifiedScript(entry=_entry(), fd=0)
    vs.close()
    assert closed == [0]  # `> 0` / `>= 1` would leak fd 0
    assert vs.fd == -1
    vs.close()  # idempotent — already closed
    assert closed == [0]


# --- FileIdentity.from_dict ---


@allure.title("FileIdentity.from_dict: exact refusal messages and value domain")
def test_file_identity_from_dict() -> None:
    with pytest.raises(TrustManifestError, match="^file_identity must be an object$"):
        FileIdentity.from_dict([1, 2])
    with pytest.raises(
        TrustManifestError,
        match="^file_identity must contain exactly device and inode$",
    ):
        FileIdentity.from_dict({"device": 1})
    with pytest.raises(
        TrustManifestError,
        match="^file_identity values must be non-negative integers$",
    ):
        FileIdentity.from_dict({"device": -1, "inode": 0})
    # device/inode == 0 is legal (st_dev/st_ino may be 0) — kills <= 0 / < 1.
    fi = FileIdentity.from_dict({"device": 0, "inode": 0})
    assert fi == FileIdentity(device=0, inode=0)
    with pytest.raises(TrustManifestError, match="non-negative integers"):
        FileIdentity.from_dict({"device": True, "inode": 0})


# --- TrustEntry.from_dict ---

_SHA = "b" * 64


@allure.title("TrustEntry.from_dict: every refusal message is exact")
@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"path": None}, "^trust entry path must be a string$"),
        ({"path": "./x.py"}, "^trust entry path is not canonical: './x.py'$"),
        ({"sha256": "ZZZ"}, "^trust entry sha256 must be 64 lowercase hex characters$"),
        ({"script_type": "exe"}, "^trust entry script_type must be python or shell$"),
        (
            {"path": "scripts/x.sh", "script_type": "python"},
            "^script_type 'python' does not match 'scripts/x.sh'$",
        ),
        ({"approved_at": ""}, "^trust entry approved_at must be a timestamp$"),
        ({"approved_at": "nope"}, "^trust entry approved_at is not ISO-8601$"),
        (
            {"approved_at": "2024-01-01T00:00:00"},
            "^trust entry approved_at must include a timezone$",
        ),
        ({"approval_source": " "}, "^trust entry approval_source must be non-empty$"),
        ({"note": 5}, "^trust entry note must be a string$"),
    ],
)
def test_trust_entry_refusal_messages(over: dict, message: str) -> None:
    with pytest.raises(TrustManifestError, match=message):
        TrustEntry.from_dict(_entry_dict(**over))


@allure.title("TrustEntry.from_dict: object/missing/unknown guards are exact")
def test_trust_entry_structural_guards() -> None:
    with pytest.raises(
        TrustManifestError, match="^each scripts entry must be an object$"
    ):
        TrustEntry.from_dict("x")
    missing = _entry_dict()
    missing.pop("sha256")
    missing.pop("approval_source")
    missing.pop("note", None)
    # Two missing fields expose the join separator — 'XX, XX'.join diverges.
    with pytest.raises(
        TrustManifestError,
        match="^trust entry is missing fields: approval_source, sha256$",
    ):
        TrustEntry.from_dict(missing)
    unknown = _entry_dict(extra_a="y", extra_b="z")
    with pytest.raises(
        TrustManifestError,
        match="^trust entry has unknown fields: extra_a, extra_b$",
    ):
        TrustEntry.from_dict(unknown)


@allure.title("TrustEntry.from_dict: absent note defaults to empty string")
def test_trust_entry_note_default() -> None:
    d = _entry_dict()
    d.pop("note", None)
    entry = TrustEntry.from_dict(d)
    # 'XXXX' default would surface in entry.note.
    assert entry.note == ""


# --- normalize_manifest_path / script_type_for_path ---


@allure.title("normalize_manifest_path: drive-letter paths count as absolute")
def test_normalize_manifest_path_drive_letter() -> None:
    # 'C:/x' is absolute only for ntpath — turning `or` into `and` lets it
    # slip through as a canonical relative path.
    with pytest.raises(TrustManifestError, match="absolute script paths"):
        trust.normalize_manifest_path("C:/x")


@allure.title("script_type_for_path: rejection message is exact")
def test_script_type_for_path_message() -> None:
    with pytest.raises(
        TrustManifestError, match="^trusted script must end in .py or .sh$"
    ):
        trust.script_type_for_path("x.txt")


# --- capability predicates ---


@allure.title("_secure_dir_fd_supported: full conjunction on posix hosts")
def test_secure_dir_fd_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "posix":
        # `or`/`!=`/XXposixXX/hasattr(None)/XXATTRXX mutants break this.
        assert trust._secure_dir_fd_supported() is True
    monkeypatch.setattr(os, "name", "nt")
    assert trust._secure_dir_fd_supported() is False


@allure.title("_fd_execution_supported: posix gate is a conjunction")
def test_fd_execution_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "posix":
        assert Path("/dev/fd").is_dir()  # test-host invariant
        assert trust._fd_execution_supported() is True
    monkeypatch.setattr(os, "name", "nt")
    assert trust._fd_execution_supported() is False


# --- path/id helpers ---


@allure.title("trust_manifest_path: trust/<wsid>/manifest.json layout is exact")
def test_trust_manifest_path_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "ws"
    root.mkdir()
    path = trust.trust_manifest_path(root)
    assert path.parent.name == trust._workspace_id(root)
    assert path.parent.parent.name == "trust"  # kills TRUST
    assert path.name == "manifest.json"


@allure.title("_trusted_runner_path: filename is exact")
def test_trusted_runner_path() -> None:
    p = trust._trusted_runner_path()
    assert p.name == "_trusted_runner.py"
    assert p.is_file()


@allure.title("_utc_now_iso: second-precision Z-suffixed UTC")
def test_utc_now_iso_format() -> None:
    import re

    # microsecond=1 leaks '.000001' into the timestamp.
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", trust._utc_now_iso()
    )


@allure.title("_sha256_fd: hashes the full file from offset 0")
def test_sha256_fd(tmp_path: Path) -> None:
    import hashlib

    f = tmp_path / "f.bin"
    f.write_bytes(b"abcdef")
    fd = os.open(f, os.O_RDONLY)
    try:
        os.lseek(fd, 3, os.SEEK_SET)  # ensure the function seeks back itself
        assert trust._sha256_fd(fd) == hashlib.sha256(b"abcdef").hexdigest()
        # The contract rewinds the fd to 0 after hashing — a mutant seeking
        # to 1 leaves the caller at a shifted position.
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
    finally:
        os.close(fd)


# --- _open_portable_nofollow refusal codes ---


@allure.title("_open_portable_nofollow: symlink component → code 'symlink'")
def test_open_portable_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.sh"
    real.write_text("x\n", encoding="utf-8")
    (tmp_path / "link.sh").symlink_to(real)
    with pytest.raises(TrustVerificationError) as exc_info:
        trust._open_portable_nofollow(tmp_path, "link.sh")
    assert exc_info.value.code == "symlink"
    assert "symlink" in str(exc_info.value)


@allure.title("_open_portable_nofollow: missing path → code 'missing_file'")
def test_open_portable_missing(tmp_path: Path) -> None:
    with pytest.raises(TrustVerificationError) as exc_info:
        trust._open_portable_nofollow(tmp_path, "gone/x.py")
    assert exc_info.value.code == "missing_file"


@allure.title("_open_portable_nofollow: directory target → refusal code")
def test_open_portable_not_regular(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    with pytest.raises(TrustVerificationError) as exc_info:
        trust._open_portable_nofollow(tmp_path, "d")
    # POSIX opens a directory then rejects it on fstat; Windows cannot
    # os.open() a directory at all → the OSError maps to 'missing_file'.
    expected = "untrusted_type" if os.name == "posix" else "missing_file"
    assert exc_info.value.code == expected


@allure.title("_open_portable_nofollow: lstat/fstat identity drift → 'stale_identity'")
def test_open_portable_stale_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.py"
    f.write_text("x\n", encoding="utf-8")
    real_lstat = Path.lstat
    # Fake lstat returns a *different* device/inode for the opened candidate —
    # simulating a swap between the stat and the open.
    fake = os.stat_result(
        (stat_mod.S_IFREG | 0o644, 99999999, 99999998, 1, 0, 0, 1, 0, 0, 0)
    )

    def patched(self, *a, **k):
        if self == f:
            return fake
        return real_lstat(self, *a, **k)

    monkeypatch.setattr(Path, "lstat", patched)
    with pytest.raises(TrustVerificationError) as exc_info:
        trust._open_portable_nofollow(tmp_path, "x.py")
    assert exc_info.value.code == "stale_identity"


# --- _write_entries / _read_entries round-trip semantics ---


def _manifest_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(tmp_path / "home"))
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return root


@allure.title("_write_entries: dir mode 0o700, temp naming, manifest mode 0o600")
def test_write_entries_modes_and_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _manifest_root(tmp_path, monkeypatch)
    seen: dict = {}
    real_mkstemp = tempfile.mkstemp

    def spy(**kwargs):
        seen.update(kwargs)
        return real_mkstemp(**kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    opened: dict = {}
    real_fdopen = os.fdopen

    def fd_spy(fd, mode, **kwargs):
        opened.update({"mode": mode, **kwargs})
        return real_fdopen(fd, mode, **kwargs)

    monkeypatch.setattr(os, "fdopen", fd_spy)
    chmod_calls: list[tuple[str, int]] = []
    real_chmod = os.chmod

    def chmod_spy(p, mode, *a, **k):
        chmod_calls.append((Path(p).name, stat_mod.S_IMODE(mode) if isinstance(mode, int) else mode))
        return real_chmod(p, mode, *a, **k)

    monkeypatch.setattr(os, "chmod", chmod_spy)
    path = trust._write_entries(root, (_entry(),))
    # Kills dropped mode / 0o601 — stat-visible only on POSIX (Windows chmod
    # is limited to the read-only bit); the chmod-call spy below covers nt.
    if os.name == "posix":
        assert stat_mod.S_IMODE(path.parent.stat().st_mode) == 0o700
    # Kills dropped/None/XX variants of prefix, suffix, dir.
    assert seen == {
        "prefix": ".manifest.", "suffix": ".tmp", "dir": path.parent
    }
    # Kills encoding=None / dropped / "UTF-8" and newline=None / dropped —
    # the manifest is pinned to UTF-8 + LF bytes by contract.
    assert opened == {"mode": "w", "encoding": "utf-8", "newline": ""}
    # Kills chmod 0o605 — the temp file must be 0o600 *before* the manifest
    # bytes are written; the post-replace chmod does not repair the window.
    assert [mode for _, mode in chmod_calls] == [0o600, 0o600]
    assert chmod_calls[0][0].endswith(".tmp")
    assert chmod_calls[1][0] == "manifest.json"
    if os.name == "posix":
        assert stat_mod.S_IMODE(path.stat().st_mode) == 0o600
    assert path.is_file()


@allure.title("_write_entries: indent 2, LF newline, raw UTF-8 (ensure_ascii=False)")
def test_write_entries_json_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _manifest_root(tmp_path, monkeypatch)
    entry = _entry(note="заметка")
    path = trust._write_entries(root, (entry,))
    raw = path.read_text(encoding="utf-8")
    # indent=None/dropped collapses the layout; indent=3 still contains the
    # bare "  " substring, so anchor at the line start to catch it.
    assert '\n  "scripts": [' in raw
    assert raw.endswith("\n")
    assert "\r\n" not in raw
    # ensure_ascii=True/None/dropped would escape the Cyrillic note.
    assert "заметка" in raw
    assert "\\u" not in raw


@allure.title("_write_entries: post-replace chmod failure is not masked")
def test_write_entries_cleanup_missing_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _manifest_root(tmp_path, monkeypatch)
    real_chmod = os.chmod

    def flaky(p, *a, **k):
        if str(p).endswith("manifest.json"):
            raise OSError("denied")
        return real_chmod(p, *a, **k)

    monkeypatch.setattr(os, "chmod", flaky)
    # After os.replace the temp path is gone; unlink(missing_ok=False) would
    # raise FileNotFoundError and mask the real chmod failure.
    with pytest.raises(OSError, match="denied"):
        trust._write_entries(root, (_entry(),))


@allure.title("_read_entries: manifest round-trip keeps entries verbatim")
def test_read_entries_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _manifest_root(tmp_path, monkeypatch)
    entries = (_entry(), _entry(path="scripts/y.sh", script_type="shell"))
    trust._write_entries(root, entries)
    back = trust._read_entries(root)
    assert back == entries


@allure.title("_read_entries: manifest is decoded with an explicit UTF-8 pin")
def test_read_entries_pinned_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import codecs

    root = _manifest_root(tmp_path, monkeypatch)
    trust._write_entries(root, (_entry(),))
    seen: dict = {}
    real_read_text = Path.read_text

    def spy(self, *a, **k):
        if self.name == "manifest.json":
            seen["encoding"] = k.get("encoding", a[0] if a else None)
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    assert trust._read_entries(root) == (_entry(),)
    # encoding=None would silently decode with the platform locale — the
    # contract pins UTF-8. codecs.lookup accepts any case spelling, so the
    # "UTF-8" case-flip equivalent is not killed here.
    assert codecs.lookup(seen["encoding"]).name == "utf-8"


# --- verify_script / verify_trust_manifest refusal codes ---


@allure.title("verify_script: entry lookup and stale-bytes refusal codes")
def test_verify_script_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    root = _manifest_root(tmp_path, monkeypatch)
    script = root / "scripts" / "x.py"
    script.parent.mkdir(parents=True)
    script.write_text("v1\n", encoding="utf-8")
    st = script.stat()
    with allure.step("unapproved path → 'not_approved'"):
        with pytest.raises(TrustVerificationError) as exc_info:
            trust.verify_script(root, "scripts/x.py")
        assert exc_info.value.code == "not_approved"
    with allure.step("hash drift → 'stale_bytes'"):
        entry = _entry(
            path="scripts/x.py",
            sha256=hashlib.sha256(b"OTHER\n").hexdigest(),
            file_identity=FileIdentity.from_stat(st),
        )
        trust._write_entries(root, (entry,))
        with pytest.raises(TrustVerificationError) as exc_info2:
            trust.verify_script(root, "scripts/x.py")
        assert exc_info2.value.code == "stale_bytes"


@allure.title("verify_trust_manifest: failed checks keep the entry reference")
def test_verify_trust_manifest_entry_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    root = _manifest_root(tmp_path, monkeypatch)
    script = root / "scripts" / "x.py"
    script.parent.mkdir(parents=True)
    script.write_text("v1\n", encoding="utf-8")
    st = script.stat()
    entry = _entry(
        path="scripts/x.py",
        sha256=hashlib.sha256(b"DIFFERENT\n").hexdigest(),  # stale bytes
        file_identity=FileIdentity.from_stat(st),
    )
    trust._write_entries(root, (entry,))
    checks = trust.verify_trust_manifest(root)
    assert len(checks) == 1
    # kills TrustCheck(entry=None): the check must carry the failing entry
    # (re-read from disk — an equal instance, not the same object).
    assert checks[0].entry == entry
    assert checks[0].entry is not None
    assert checks[0].ok is False
    assert checks[0].code == "stale_bytes"


@allure.title("verify_trust_manifest: TrustError arm keeps the entry reference")
def test_verify_trust_manifest_trust_error_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _manifest_root(tmp_path, monkeypatch)
    entry = _entry()
    trust._write_entries(root, (entry,))
    real_read = trust._read_entries
    calls = {"n": 0}

    def flaky(r):
        # The outer listing succeeds; verify_script's internal re-read of the
        # manifest raises TrustManifestError — exercising `except TrustError`.
        calls["n"] += 1
        if calls["n"] > 1:
            raise TrustManifestError("manifest vanished mid-verify")
        return real_read(r)

    monkeypatch.setattr(trust, "_read_entries", flaky)
    checks = trust.verify_trust_manifest(root)
    assert len(checks) == 1
    # kills the TrustError-arm TrustCheck(entry=None) mutant.
    assert checks[0].entry == entry
    assert checks[0].entry is not None
    assert checks[0].ok is False
