"""Execute a trust-verified Python script from an inherited file descriptor.

Usage: ``python _trusted_runner.py <fd> <script-path> [args...]``

The fd was opened and hash-verified by the parent before spawn and is
inherited via ``pass_fds`` — reading it yields exactly the approved bytes,
with no path re-open and no TOCTOU window.  The runner then reproduces the
``python <script>`` import context that ``/dev/fd/N`` execution loses:
``sys.argv[0]`` and ``__file__`` name the real script path, and the script's
directory leads ``sys.path`` so sibling imports keep working.
"""

from __future__ import annotations

import os
import sys

_READ_CHUNK = 128 * 1024


def main() -> None:
    observed = bool(os.environ.get("GREEDY_TOKEN_OBSERVE", "").strip())
    if observed:
        # Bootstrap guards/channel before any product or script code runs.
        from greedy_token.cheap_llm import install_observation

        install_observation()

    fd = int(sys.argv[1])
    script_path = sys.argv[2]
    script_args = sys.argv[3:]

    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, _READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
    fd_stat = os.fstat(fd)
    os.close(fd)
    source = b"".join(chunks)

    if observed:
        # Bind the consumed buffer — the same bytes compile() gets — back
        # to the parent's admission record; never a second path read.
        from greedy_token.cheap_llm import observe_trusted_child_bind

        observe_trusted_child_bind(
            source=source,
            script_path=script_path,
            fd_identity=(fd_stat.st_dev, fd_stat.st_ino),
            runner_path=os.path.abspath(__file__),
        )

    sys.argv = [script_path, *script_args]
    script_dir = os.path.dirname(os.path.abspath(script_path))
    sys.path.insert(0, script_dir)

    code = compile(source, script_path, "exec")
    globals_dict = {
        "__name__": "__main__",
        "__file__": script_path,
        "__package__": None,
        "__spec__": None,
        "__cached__": None,
    }
    exec(code, globals_dict)


if __name__ == "__main__":
    main()
