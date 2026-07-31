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


def main() -> int:
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
