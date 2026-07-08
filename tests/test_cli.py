from __future__ import annotations

import subprocess
import sys

import allure
import pytest

pytestmark = [allure.epic("CLI"), allure.feature("greedy-token CLI")]


@allure.story("Help")
@allure.title("CLI --help lists route command")
def test_cli_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "greedy_token", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "route" in proc.stdout


@allure.story("Pipeline")
@allure.title("CLI pipeline --list shows named recipes")
def test_cli_pipeline_list() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "greedy_token", "pipeline", "--list"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "meta-audit" in proc.stdout


@allure.story("Route")
@allure.title("CLI route recommends tool tier for find task")
def test_cli_route_tool(minimal_workspace) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "greedy_token",
            "--no-log",
            "route",
            "find baseUrl in sample.js",
        ],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "GREEDY_TOKEN_ROOT": str(minimal_workspace)},
    )
    assert proc.returncode == 0
    assert "TOOL" in proc.stdout or "tool" in proc.stdout.lower()
