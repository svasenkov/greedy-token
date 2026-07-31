"""Require the exact release commit's mandatory CI matrix gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request


REQUIRED_JOB = "required matrix gate"


def api_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_release_matrix.py <release-tag>")
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    tag = sys.argv[1]
    sha = subprocess.run(
        ["git", "rev-list", "-n", "1", f"{tag}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()
    query = urllib.parse.urlencode(
        {
            "event": "push",
            "head_sha": sha,
            "status": "completed",
            "per_page": 100,
        }
    )
    runs = api_json(
        f"https://api.github.com/repos/{repository}/actions/workflows/test.yml/runs?{query}",
        token,
    ).get("workflow_runs", [])
    run = next(
        (item for item in runs if item.get("head_sha") == sha and item.get("event") == "push"),
        None,
    )
    if run is None or run.get("conclusion") != "success":
        conclusion = "missing" if run is None else run.get("conclusion")
        raise SystemExit(
            f"Refusing publish: Test workflow for {sha} is {conclusion}."
        )

    jobs = api_json(f"{run['jobs_url']}?per_page=100", token).get("jobs", [])
    gate = next((job for job in jobs if job.get("name") == REQUIRED_JOB), None)
    if gate is None or gate.get("conclusion") != "success":
        conclusion = "missing" if gate is None else gate.get("conclusion")
        raise SystemExit(
            f"Refusing publish: {REQUIRED_JOB!r} for {sha} is {conclusion}."
        )
    print(f"Mandatory matrix gate passed for {sha}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
