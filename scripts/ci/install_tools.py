"""Install CI command-line tools without shell-specific workflow steps."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys


def run(argv: list[str]) -> None:
    print("+", argv)
    subprocess.run(argv, check=True, shell=False)


def install(tool: str) -> None:
    if shutil.which(tool):
        print(f"{tool}: already available")
        return

    system = platform.system()
    if system == "Linux":
        run(["sudo", "apt-get", "update"])
        run(["sudo", "apt-get", "install", "-y", "ripgrep" if tool == "rg" else "jq"])
    elif system == "Darwin":
        run(["brew", "install", "ripgrep" if tool == "rg" else "jq"])
    elif system == "Windows":
        run(["choco", "install", "-y", "ripgrep" if tool == "rg" else "jq"])
    else:
        raise RuntimeError(f"unsupported CI platform: {system}")

    if not shutil.which(tool):
        raise RuntimeError(f"{tool} was installed but is not discoverable on PATH")


def main() -> int:
    tools = sys.argv[1:] or ["rg", "jq"]
    unsupported = sorted(set(tools) - {"rg", "jq"})
    if unsupported:
        raise SystemExit(f"unsupported tools: {', '.join(unsupported)}")
    for tool in tools:
        install(tool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
