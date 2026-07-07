"""Filter ripgrep output — shared by executors and code_search."""

from __future__ import annotations

JUNK_TOOL_PATH_FRAGMENTS = (
    ".cursor/hooks/",
    "greedy-token-route.sh",
    "greedy-token-home/dev/README",
)


def filter_tool_output(output: str) -> str:
    lines: list[str] = []
    for line in output.splitlines():
        if any(fragment in line for fragment in JUNK_TOOL_PATH_FRAGMENTS):
            continue
        if line.strip():
            lines.append(line)
    return "\n".join(lines).strip()
