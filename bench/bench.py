"""Reproducible performance baseline for greedy-token CLI.

Usage:
    python bench/bench.py [--runs 5] [--stress]

Requires: greedy-token installed in the current interpreter's environment
(`pip install -e .`), GREEDY_TOKEN_ROOT set (or auto-detected).

--stress additionally runs `tokens .` over the whole workspace root
(slow: tens of seconds on a large workspace).
"""
from __future__ import annotations

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

CLI = shutil.which("greedy-token") or os.path.join(
    os.path.dirname(sys.executable), "greedy-token"
)

COMMANDS = [
    ("help (cold start)", ["--help"]),
    ("route", ["route", "find baseUrl"]),
    ("estimate", ["estimate", "refactor header layout"]),
    ("estimate + log", ["estimate", "find baseUrl"]),
    ("estimate --no-log", ["--no-log", "estimate", "find baseUrl"]),
    ("rag", ["rag", "config baseUrl healthCheck"]),
    ("tokens (small dir)", ["tokens", ".cursor/rules"]),
    ("audit-context", ["audit-context"]),
]

PERF_GATE = 1.05


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--stress", action="store_true")
    opts = parser.parse_args()

    root = os.environ.get("GREEDY_TOKEN_ROOT", os.getcwd())
    log_file = os.path.join(tempfile.gettempdir(), "greedy-token-bench.jsonl")
    env = os.environ.copy()
    env["GREEDY_TOKEN_LOG"] = log_file

    print(f"cli={CLI}")
    print(f"root={root}")
    print(f"python={sys.version.split()[0]}  runs={opts.runs}\n")

    subprocess.run(
        [CLI, "tokens", "README.md"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    results: dict[str, tuple[float, float]] = {}
    print(f"{'command':<24} {'median':>9} {'best':>9}")
    print("-" * 44)
    for label, args in COMMANDS:
        use_env = env if "log" in label and "--no-log" not in label else os.environ
        median, best = time_command_in(args, root, opts.runs, use_env)
        results[label] = (median, best)
        print(f"{label:<24} {median:>8.3f}s {best:>8.3f}s")

    logged = results.get("estimate + log")
    baseline = results.get("estimate")
    if logged and baseline and baseline[0] > 0:
        ratio = logged[0] / baseline[0]
        status = "PASS" if ratio <= PERF_GATE else "FAIL"
        print(
            f"\nperf gate estimate+log: {ratio:.3f}x baseline "
            f"(limit {PERF_GATE:.2f}x) — {status}"
        )

    if opts.stress:
        print("\nstress: tokens . (whole root)")
        median, best = time_command_in(["tokens", "."], root, runs=1, env=os.environ)
        print(f"{'tokens . (1 run)':<24} {median:>8.3f}s")


def time_command_in(
    args: list[str],
    cwd: str,
    runs: int,
    env: dict[str, str] | None = None,
) -> tuple[float, float]:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        subprocess.run(
            [CLI, *args],
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples), min(samples)


if __name__ == "__main__":
    main()
