"""Local, content-bound approval for workspace scripts.

Trust manifests live outside the workspace so route/config presets cannot grant
execution authority by writing project files.  Every approved script is bound
to a workspace-relative path, SHA-256 digest, script type, approval metadata,
and the file identity observed during approval.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

MANIFEST_VERSION = 1
MANIFEST_MAX_BYTES = 1024 * 1024
HASH_CHUNK_BYTES = 128 * 1024
APPROVAL_SOURCE_CLI = "local-cli"
ScriptType = Literal["python", "shell"]

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SCRIPT_TYPES: dict[str, ScriptType] = {".py": "python", ".sh": "shell"}


class TrustError(ValueError):
    """A trust manifest or script violates the local security contract."""


class TrustManifestError(TrustError):
    """The local manifest is malformed or unsafe."""


class TrustVerificationError(TrustError):
    """The current script no longer matches its approval."""


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileIdentity:
        return cls(device=int(value.st_dev), inode=int(value.st_ino))

    @classmethod
    def from_dict(cls, value: object) -> FileIdentity:
        if not isinstance(value, dict):
            raise TrustManifestError("file_identity must be an object")
        if set(value) != {"device", "inode"}:
            raise TrustManifestError(
                "file_identity must contain exactly device and inode"
            )
        device = value["device"]
        inode = value["inode"]
        if (
            not isinstance(device, int)
            or isinstance(device, bool)
            or device < 0
            or not isinstance(inode, int)
            or isinstance(inode, bool)
            or inode < 0
        ):
            raise TrustManifestError("file_identity values must be non-negative integers")
        return cls(device=device, inode=inode)

    def to_dict(self) -> dict[str, int]:
        return {"device": self.device, "inode": self.inode}


@dataclass(frozen=True)
class TrustEntry:
    path: str
    sha256: str
    script_type: ScriptType
    approved_at: str
    approval_source: str
    file_identity: FileIdentity
    note: str = ""

    @classmethod
    def from_dict(cls, value: object) -> TrustEntry:
        if not isinstance(value, dict):
            raise TrustManifestError("each scripts entry must be an object")
        required = {
            "path",
            "sha256",
            "script_type",
            "approved_at",
            "approval_source",
            "file_identity",
        }
        allowed = required | {"note"}
        missing = required - set(value)
        unknown = set(value) - allowed
        if missing:
            raise TrustManifestError(
                f"trust entry is missing fields: {', '.join(sorted(missing))}"
            )
        if unknown:
            raise TrustManifestError(
                f"trust entry has unknown fields: {', '.join(sorted(unknown))}"
            )

        raw_path = value["path"]
        if not isinstance(raw_path, str):
            raise TrustManifestError("trust entry path must be a string")
        path = normalize_manifest_path(raw_path)
        if path != raw_path:
            raise TrustManifestError(f"trust entry path is not canonical: {raw_path!r}")

        digest = value["sha256"]
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise TrustManifestError("trust entry sha256 must be 64 lowercase hex characters")

        script_type = value["script_type"]
        if script_type not in ("python", "shell"):
            raise TrustManifestError("trust entry script_type must be python or shell")
        expected_type = script_type_for_path(path)
        if script_type != expected_type:
            raise TrustManifestError(
                f"script_type {script_type!r} does not match {path!r}"
            )

        approved_at = value["approved_at"]
        if not isinstance(approved_at, str) or not approved_at:
            raise TrustManifestError("trust entry approved_at must be a timestamp")
        try:
            parsed_at = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TrustManifestError("trust entry approved_at is not ISO-8601") from exc
        if parsed_at.tzinfo is None:
            raise TrustManifestError("trust entry approved_at must include a timezone")

        approval_source = value["approval_source"]
        if not isinstance(approval_source, str) or not approval_source.strip():
            raise TrustManifestError("trust entry approval_source must be non-empty")

        note = value.get("note", "")
        if not isinstance(note, str):
            raise TrustManifestError("trust entry note must be a string")

        return cls(
            path=path,
            sha256=digest,
            script_type=script_type,
            approved_at=approved_at,
            approval_source=approval_source,
            file_identity=FileIdentity.from_dict(value["file_identity"]),
            note=note,
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "script_type": self.script_type,
            "approved_at": self.approved_at,
            "approval_source": self.approval_source,
            "file_identity": self.file_identity.to_dict(),
        }
        if self.note:
            value["note"] = self.note
        return value


@dataclass
class VerifiedScript:
    """An approved script held open across verification and process launch."""

    entry: TrustEntry
    fd: int

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> VerifiedScript:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True)
class TrustCheck:
    entry: TrustEntry
    ok: bool
    error: str = ""


def _trust_home() -> Path:
    raw = os.environ.get("GREEDY_TOKEN_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".greedy-token"


def _workspace_id(root: Path) -> str:
    canonical = os.path.normcase(str(root.expanduser().resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def trust_manifest_path(root: Path) -> Path:
    """Return the user-local manifest path bound to *root*."""
    return _trust_home() / "trust" / _workspace_id(root) / "manifest.json"


def normalize_manifest_path(value: str | Path) -> str:
    """Canonicalise a relative POSIX manifest path without touching the file."""
    text = str(value)
    if not text or "\x00" in text:
        raise TrustManifestError("script path must be non-empty and contain no NUL")
    if Path(text).is_absolute() or ntpath.isabs(text) or ntpath.splitdrive(text)[0]:
        raise TrustManifestError("absolute script paths are not allowed in trust manifests")
    if "\\" in text:
        raise TrustManifestError("trust manifest paths must use '/' separators")
    path = PurePosixPath(text)
    if path == PurePosixPath(".") or ".." in path.parts:
        raise TrustManifestError("trust manifest path must stay inside the workspace")
    return path.as_posix()


def script_type_for_path(path: str | Path) -> ScriptType:
    suffix = PurePosixPath(str(path)).suffix
    try:
        return _SCRIPT_TYPES[suffix]
    except KeyError as exc:
        raise TrustManifestError("trusted script must end in .py or .sh") from exc


def _secure_dir_fd_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
    )


def _open_posix_nofollow(root: Path, relative_path: str) -> tuple[int, os.stat_result]:
    parts = PurePosixPath(relative_path).parts
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC

    directory_fd = os.open(root, directory_flags)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
    except OSError as exc:
        raise TrustVerificationError(
            f"script path is missing, replaced, or contains a symlink: {relative_path!r}"
        ) from exc
    finally:
        os.close(directory_fd)

    file_stat = os.fstat(file_fd)
    if not stat.S_ISREG(file_stat.st_mode):
        os.close(file_fd)
        raise TrustVerificationError(f"trusted script is not a regular file: {relative_path!r}")
    return file_fd, file_stat


def _open_portable_nofollow(root: Path, relative_path: str) -> tuple[int, os.stat_result]:
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    file_fd = None
    try:
        for part in PurePosixPath(relative_path).parts:
            current = current / part
            current_stat = current.lstat()
            if stat.S_ISLNK(current_stat.st_mode):
                raise TrustVerificationError(
                    f"script path contains a symlink: {relative_path!r}"
                )
        resolved = candidate.resolve()
        resolved.relative_to(root)
        file_fd = os.open(candidate, os.O_RDONLY)
        path_stat = candidate.lstat()
        file_stat = os.fstat(file_fd)
    except TrustVerificationError:
        if file_fd is not None:
            os.close(file_fd)
        raise
    except (OSError, ValueError) as exc:
        if file_fd is not None:
            os.close(file_fd)
        raise TrustVerificationError(
            f"script path is missing, replaced, or outside workspace: {relative_path!r}"
        ) from exc

    if (
        stat.S_ISLNK(path_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or FileIdentity.from_stat(path_stat) != FileIdentity.from_stat(file_stat)
    ):
        os.close(file_fd)
        raise TrustVerificationError(
            f"script path changed while it was opened: {relative_path!r}"
        )
    return file_fd, file_stat


def _open_script(root: Path, relative_path: str) -> tuple[int, os.stat_result]:
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        raise TrustVerificationError(f"workspace root is not a directory: {resolved_root}")
    path = normalize_manifest_path(relative_path)
    if _secure_dir_fd_supported():
        return _open_posix_nofollow(resolved_root, path)
    return _open_portable_nofollow(resolved_root, path)


def _sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, HASH_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _read_entries(root: Path) -> tuple[TrustEntry, ...]:
    path = trust_manifest_path(root)
    if not path.exists():
        return ()
    if path.is_symlink():
        raise TrustManifestError(f"trust manifest must not be a symlink: {path}")
    try:
        if path.stat().st_size > MANIFEST_MAX_BYTES:
            raise TrustManifestError("trust manifest exceeds 1 MiB")
        # equivalent: valid UTF-8 manifests decode identically with utf-8, UTF-8, or the UTF-8 locale default.
        raw = json.loads(path.read_text(encoding="utf-8"))  # pragma: no mutate
    except TrustManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrustManifestError(f"cannot read trust manifest: {path}") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "scripts"}:
        raise TrustManifestError("trust manifest must contain exactly version and scripts")
    if raw["version"] != MANIFEST_VERSION:
        raise TrustManifestError(
            f"unsupported trust manifest version: {raw['version']!r}"
        )
    scripts = raw["scripts"]
    if not isinstance(scripts, list):
        raise TrustManifestError("trust manifest scripts must be a list")
    entries = tuple(TrustEntry.from_dict(value) for value in scripts)
    paths = [entry.path for entry in entries]
    if len(paths) != len(set(paths)):
        raise TrustManifestError("trust manifest contains duplicate script paths")
    return entries


def _write_entries(root: Path, entries: tuple[TrustEntry, ...]) -> Path:
    path = trust_manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise TrustManifestError(f"trust manifest must not be a symlink: {path}")
    payload = {
        "version": MANIFEST_VERSION,
        "scripts": [
            entry.to_dict() for entry in sorted(entries, key=lambda item: item.path)
        ],
    }
    fd, temp_name = tempfile.mkstemp(
        prefix=".manifest.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise
    return path


def list_trust_entries(root: Path) -> tuple[TrustEntry, ...]:
    return _read_entries(root)


def trusted_manifest_paths(root: Path) -> frozenset[str]:
    return frozenset(entry.path for entry in _read_entries(root))


def approve_script(
    root: Path,
    path: str | Path,
    *,
    note: str | None = None,
    approval_source: str = APPROVAL_SOURCE_CLI,
) -> TrustEntry:
    """Approve the current bytes and file identity of a local script."""
    relative_path = normalize_manifest_path(path)
    script_type = script_type_for_path(relative_path)
    if not approval_source.strip():
        raise TrustManifestError("approval source must be non-empty")
    entries = _read_entries(root)
    previous = next((entry for entry in entries if entry.path == relative_path), None)
    file_fd, file_stat = _open_script(root, relative_path)
    try:
        digest = _sha256_fd(file_fd)
    finally:
        os.close(file_fd)
    entry = TrustEntry(
        path=relative_path,
        sha256=digest,
        script_type=script_type,
        approved_at=_utc_now_iso(),
        approval_source=approval_source,
        file_identity=FileIdentity.from_stat(file_stat),
        note=previous.note if note is None and previous is not None else (note or ""),
    )
    kept = tuple(existing for existing in entries if existing.path != relative_path)
    _write_entries(root, (*kept, entry))
    return entry


def revoke_script(root: Path, path: str | Path) -> bool:
    relative_path = normalize_manifest_path(path)
    entries = _read_entries(root)
    kept = tuple(entry for entry in entries if entry.path != relative_path)
    if len(kept) == len(entries):
        return False
    _write_entries(root, kept)
    return True


def verify_script(root: Path, path: str | Path) -> VerifiedScript:
    """Open and verify one entry; the caller must keep/close the returned FD."""
    relative_path = normalize_manifest_path(path)
    entries = _read_entries(root)
    entry = next((item for item in entries if item.path == relative_path), None)
    if entry is None:
        raise TrustVerificationError(
            f"script is not approved in the local trust manifest: {relative_path!r}"
        )
    file_fd, file_stat = _open_script(root, relative_path)
    try:
        actual_hash = _sha256_fd(file_fd)
        if actual_hash != entry.sha256:
            raise TrustVerificationError(
                f"SHA-256 mismatch for {relative_path!r}; run 'greedy-token trust add' after review"
            )
        if FileIdentity.from_stat(file_stat) != entry.file_identity:
            raise TrustVerificationError(
                f"file identity changed for {relative_path!r}; re-approval is required"
            )
        if script_type_for_path(relative_path) != entry.script_type:
            raise TrustVerificationError(
                f"script type changed for {relative_path!r}; re-approval is required"
            )
    except BaseException:
        os.close(file_fd)
        raise
    return VerifiedScript(entry=entry, fd=file_fd)


def verify_trust_manifest(root: Path) -> tuple[TrustCheck, ...]:
    checks: list[TrustCheck] = []
    for entry in _read_entries(root):
        try:
            with verify_script(root, entry.path):
                pass
        except TrustError as exc:
            checks.append(TrustCheck(entry=entry, ok=False, error=str(exc)))
        else:
            checks.append(TrustCheck(entry=entry, ok=True))
    return tuple(checks)


def _fd_execution_supported() -> bool:
    return os.name == "posix" and Path("/dev/fd").is_dir()


def bind_verified_argv(
    verified: VerifiedScript, argv: tuple[str, ...]
) -> tuple[list[str], tuple[int, ...]]:
    """Bind execution to the verified descriptor where the platform supports it."""
    bound = list(argv)
    if not _fd_execution_supported():
        return bound, ()
    descriptor_path = f"/dev/fd/{verified.fd}"
    if verified.entry.script_type == "python":
        if len(bound) < 2:
            raise TrustVerificationError("verified Python invocation has no script argv")
        bound[1] = descriptor_path
    else:
        if not bound:
            raise TrustVerificationError("verified shell invocation has empty argv")
        bound[0] = descriptor_path
    os.lseek(verified.fd, 0, os.SEEK_SET)
    return bound, (verified.fd,)
