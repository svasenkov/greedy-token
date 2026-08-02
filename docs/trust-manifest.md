# Local script trust manifest

`read_only: true` describes a route; it does not authorize code execution.
Custom workspace scripts get execution authority only after a local human
approval:

```bash
greedy-token trust add scripts/my-check.py --note "reviewed for stdout-only IO"
greedy-token trust list
greedy-token trust verify
greedy-token trust revoke scripts/my-check.py
```

The manifest is stored outside the repository at
`~/.greedy-token/trust/<workspace-id>/manifest.json` (or below
`GREEDY_TOKEN_HOME`). The workspace ID is a SHA-256 of the canonical workspace
root. A route preset, `routes_file`, repository checkout, or workspace config
therefore cannot write an approval as part of normal preset installation.

## Schema and migration

Each version-1 entry contains:

```json
{
  "path": "scripts/my-check.py",
  "sha256": "<64 lowercase hex characters>",
  "script_type": "python",
  "approved_at": "2026-07-31T16:55:00Z",
  "approval_source": "local-cli",
  "file_identity": {"device": 1, "inode": 12345},
  "note": "optional human note"
}
```

Paths are canonical, workspace-relative POSIX paths. Absolute paths, drive
paths, `..`, backslashes, unsupported extensions, symlinks, non-regular files,
and paths outside the workspace are rejected. Script type is derived from
`.py` or `.sh`, not accepted independently from the path.

The old `.greedy-token.yaml` key `trusted_script_paths` is deprecated. It is
read only to produce a migration refusal and remains dry-run configuration.
Existing values never receive execution privilege automatically. Review each
script and run `greedy-token trust add PATH`.

## Verification at execution

For every manifest-authorized launch, greedy-token:

1. revalidates structured argv and keeps `python -c`, shell `-c`, arbitrary
   interpreters, and outside-root arguments forbidden;
2. reloads the local manifest;
3. opens every path component without following symlinks where the OS supports
   `openat`/`O_NOFOLLOW`;
4. computes SHA-256 from the opened file immediately before process launch;
5. compares the approval-time device/inode identity, catching deleted and
   recreated or path-replaced files even when their bytes are identical;
6. on POSIX, keeps that verified descriptor open and executes through
   `/dev/fd/<n>`.

Any mismatch blocks the launch until explicit re-approval. `trust verify`
performs the same checks without executing a script.

Trust commands do not emit usage events. Script bytes, stdout, approval notes,
environment values, and secrets are not copied into telemetry. Existing route
telemetry records route/executor metadata only.

## TOCTOU threat model and limits

The control protects against stale approvals, ordinary edits, symlink escapes,
path replacement, and malicious URL/file route presets. POSIX descriptor
binding closes the rename/symlink gap between verification and interpreter
open.

It is not a sandbox and does not defend against a fully compromised same-user
account:

- a same-user attacker able to rewrite both the user-local manifest and the
  workspace can forge a new approval;
- a concurrent same-inode writer can still change an already-open file after
  hashing; POSIX `/dev/fd` binds identity, but does not snapshot mutable bytes;
- Windows uses the portable no-symlink/identity/hash checks but cannot pass an
  executable descriptor with `pass_fds`, leaving a narrow verify-to-open
  TOCTOU window;
- an approved script can perform any operation allowed to the current OS user.
  `read_only` is a review contract, not process isolation.

For hostile multi-user workspaces, copy reviewed scripts to an immutable,
administrator-owned location or run greedy-token inside an OS sandbox. Re-run
`trust verify` after filesystem or checkout operations that replace files.
