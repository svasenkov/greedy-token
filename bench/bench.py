"""Reproducible performance baseline for greedy-token CLI.

Usage:
    python bench/bench.py [--runs 5] [--stress]

Requires: greedy-token installed in the current interpreter's environment
(`pip install -e .[tiktoken]`), GREEDY_TOKEN_ROOT set (or auto-detected).

--stress additionally runs `tokens .` over the whole workspace root
(slow: tens of seconds on a large monorepo).
"""
from __future__ import annotations

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import time

CLI = shutil.which("greedy-token") or os.path.join(
    os.path.dirname(sys.executable), "greedy-token"
)

COMMANDS = [
    ("help (cold start)", ["--help"]),
    ("route", ["route", "find baseUrl"]),
    ("estimate", ["estimate", "refactor header layout"]),
    ("rag", ["rag", "e2e baseUrl healthCheck"]),
    ("tokens (small dir)", ["tokens", ".cursor/rules"]),
    ("audit-context", ["audit-context"]),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--stress", action="store_true")
    opts = parser.parse_args()

    root = os.environ.get("GREEDY_TOKEN_ROOT", os.getcwd())
    print(f"cli={CLI}")
    print(f"root={root}")
    print(f"python={sys.version.split()[0]}  runs={opts.runs}\n")

    # Warm run so first-use tiktoken BPE download does not skew results
    subprocess.run(
        [CLI, "tokens", "README.md"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    print(f"{'command':<24} {'median':>9} {'best':>9}")
    print("-" * 44)
    for label, args in COMMANDS:
        median, best = time_command_in(args, root, opts.runs)
        print(f"{label:<24} {median:>8.3f}s {best:>8.3f}s")

    if opts.stress:
        print("\nstress: tokens . (whole root)")
        median, best = time_command_in(["tokens", "."], root, runs=1)
        print(f"{'tokens . (1 run)':<24} {median:>8.3f}s")


def time_command_in(args: list[str], cwd: str, runs: int) -> tuple[float, float]:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        subprocess.run(
            [CLI, *args],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples), min(samples)


if __name__ == "__main__":
    main()
