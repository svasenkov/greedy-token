"""Run the tests relevant to a dependency compatibility profile."""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata


MCP_TESTS = [
    "tests/test_mcp_gaps.py",
    "tests/test_mcp_handlers.py",
    "tests/test_mcp_icon.py",
    "tests/test_mcp_stdio.py",
    "tests/test_mcp_tools.py",
]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_profile.py <profile>")
    profile = sys.argv[1]
    if profile == "minimum":
        expected = {
            "PyYAML": "6.0.1",
            "tiktoken": "0.7.0",
            "pytest": "8.0.0",
            "allure-pytest": "2.16.0",
            "coverage": "7.0.0",
            "hypothesis": "6.10.1",
            "mcp": "1.15.0",
        }
        installed = {name: metadata.version(name) for name in expected}
        if installed != expected:
            raise SystemExit(f"minimum dependency mismatch: {installed!r}")
    elif profile == "mcp-lowest":
        if metadata.version("mcp") != "1.15.0":
            raise SystemExit("mcp-lowest must install exactly 1.15.0")
    elif profile == "mcp-latest":
        version = tuple(int(part) for part in metadata.version("mcp").split(".")[:2])
        if not (version >= (1, 15) and version < (2, 0)):
            raise SystemExit(f"mcp-latest resolved outside supported 1.x: {version!r}")
    tests = MCP_TESTS if profile.startswith("mcp-") else ["tests/", "-m", "unit"]
    subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q"],
        check=True,
        shell=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
