"""Install one dependency compatibility profile using structured argv."""

from __future__ import annotations

import subprocess
import sys

# mcp 1.22.0 is the first release without the removed pydantic internal
# `eval_type_backport` import (breaks under pydantic >=2.13).
MCP_LOWEST = "1.22.0"
MINIMUM = [
    "PyYAML==6.0.1",
    "pathspec==1.1.1",
    "tiktoken==0.7.0",
    "pytest==8.0.0",
    # pyproject addopts passes -n auto unconditionally — xdist is not optional.
    "pytest-xdist==3.8.0",
    "allure-pytest==2.16.0",
    "coverage==7.0.0",
    "hypothesis==6.10.1",
    f"mcp=={MCP_LOWEST}",
]


def run(*args: str) -> None:
    argv = [sys.executable, "-m", "pip", *args]
    print("+", argv)
    subprocess.run(argv, check=True, shell=False)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: install_profile.py minimum|latest|mcp-lowest|mcp-latest")
    profile = sys.argv[1]
    run("install", "--upgrade", "pip")
    if profile == "minimum":
        run("install", "-e", ".[dev]", "--no-deps")
        run("install", *MINIMUM)
    elif profile == "latest":
        run("install", "--upgrade", "-e", ".[dev,mcp]")
    elif profile == "mcp-lowest":
        run("install", "-e", ".[dev]", f"mcp=={MCP_LOWEST}")
    elif profile == "mcp-latest":
        run("install", "--upgrade", "-e", ".[dev]", f"mcp>={MCP_LOWEST},<2")
    else:
        raise SystemExit(f"unknown dependency profile: {profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
