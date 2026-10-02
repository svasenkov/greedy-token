"""Filter ripgrep output — shared by executors and code_search."""

from __future__ import annotations

JUNK_TOOL_PATH_FRAGMENTS = (
    ".cursor/hooks/",
    ".claude/hooks/",
    ".devin/hooks/",
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


# Total displayed hits cap — a repo-wide rg can flood the answer with
# self-referential noise; beyond the cap the tail is folded into a marker.
TOOL_OUTPUT_LINE_CAP = 30


def cap_tool_output(output: str, limit: int = TOOL_OUTPUT_LINE_CAP) -> str:
    """Keep at most *limit* output lines; overflow becomes a truncation marker."""
    if limit <= 0 or not output:
        return output
    lines = output.splitlines()
    if len(lines) <= limit:
        return output
    kept = lines[:limit]
    kept.append(f"… truncated — {len(lines) - limit} more line(s) (cap {limit})")
    return "\n".join(kept)
