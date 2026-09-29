"""Build wheel/sdist and smoke-install the wheel on the current OS."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(argv: list[str], *, cwd: Path = ROOT) -> None:
    print("+", argv)
    subprocess.run(argv, cwd=cwd, check=True, shell=False)


def _untracked_test_files(porcelain: str) -> list[str]:
    """Untracked ``.py`` files under ``tests/`` in ``git status --porcelain``.

    ``MANIFEST.in`` ships ``recursive-include tests *.py`` — a file that was
    never committed still lands in the sdist, so builds must start from a
    clean checkout of ``tests/``.
    """
    flagged: list[str] = []
    for line in porcelain.splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:].strip().strip('"')
        if path.startswith("tests/") and (path.endswith(".py") or path.endswith("/")):
            flagged.append(path)
    return flagged


def _guard_clean_tests_checkout() -> None:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "-uall", "--", "tests/"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return  # no git binary — nothing to guard
    if status.returncode != 0:
        return  # not a git checkout — nothing to guard
    untracked = _untracked_test_files(status.stdout)
    if untracked:
        raise SystemExit(
            "untracked tests/*.py would silently enter the sdist "
            "(MANIFEST.in can't see git status) — commit or remove them:\n"
            + "\n".join(f"  {name}" for name in untracked)
        )


def main() -> int:
    _guard_clean_tests_checkout()
    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    run([sys.executable, "-m", "pip", "install", "--upgrade", "build"])
    run([sys.executable, "-m", "build", "--wheel", "--sdist"])

    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(f"expected one wheel and one sdist, got {wheels!r}, {sdists!r}")

    with tempfile.TemporaryDirectory(prefix="greedy-token-smoke-") as temp:
        venv = Path(temp) / "venv"
        run([sys.executable, "-m", "venv", str(venv)])
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run([str(python), "-m", "pip", "install", str(wheels[0])])
        run([str(python), "-m", "greedy_token", "--help"])
        run(
            [
                str(python),
                "-c",
                "import greedy_token; print(greedy_token.__version__)",
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
