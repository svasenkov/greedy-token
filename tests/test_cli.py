from __future__ import annotations

import subprocess
import sys

import allure
import pytest

from tests.allure_reporting import attach_text

pytestmark = [
    allure.epic("CLI"),
    allure.parent_suite("CLI"),
    allure.feature("greedy-token CLI"),
    allure.suite("greedy-token CLI"),
]


@allure.story("Help")
@allure.title("CLI --help lists route command")
def test_cli_help() -> None:
    with allure.step("Run greedy-token CLI --help"):
        proc = subprocess.run(
            [sys.executable, "-m", "greedy_token", "--help"],
            capture_output=True,
            text=True,
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify help mentions route command"):
        assert proc.returncode == 0
        assert "route" in proc.stdout


@allure.story("Pipeline")
@allure.title("CLI pipeline --list shows named recipes")
def test_cli_pipeline_list() -> None:
    with allure.step("Run greedy-token pipeline --list"):
        proc = subprocess.run(
            [sys.executable, "-m", "greedy_token", "pipeline", "--list"],
            capture_output=True,
            text=True,
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify meta-audit recipe is listed"):
        assert proc.returncode == 0
        assert "meta-audit" in proc.stdout


@allure.story("Route")
@allure.title("CLI route recommends tool tier for find task")
def test_cli_route_tool(minimal_workspace) -> None:
    with allure.step("Run greedy-token route for find task"):
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
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify route recommends tool tier"):
        assert proc.returncode == 0
        assert "TOOL" in proc.stdout or "tool" in proc.stdout.lower()


@allure.story("Override")
@allure.title("CLI override emits script_override JSON")
def test_cli_override_json(minimal_workspace) -> None:
    with allure.step("Run greedy-token override in no-log JSON mode"):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "greedy_token",
                "--no-log",
                "override",
                "python-ssh-check",
                "ssh check qaguru prod",
                "--reason",
                "user_reask",
                "--json",
            ],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "GREEDY_TOKEN_ROOT": str(minimal_workspace)},
        )
        attach_text("stdout", proc.stdout)
        attach_text("stderr", proc.stderr or "")
    with allure.step("Verify override JSON contract"):
        assert proc.returncode == 0
        assert '"event": "script_override"' in proc.stdout
        assert '"crystal_id": "python-ssh-check"' in proc.stdout
