#!/usr/bin/env python
"""Public end-to-end evidence benchmark.

Deterministic mode uses a temporary workspace, a local Ollama availability
stub, the real CLI, and the real MCP stdio protocol.  Live mode is manual and
may additionally probe a real Ollama endpoint and an explicitly configured
agent-host adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from greedy_token.rag_index import invalidate_rag_index  # noqa: E402
from greedy_token.router import route_task  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "bench" / "evidence_corpus.v1.yaml"
DEFAULT_LOCK = REPO_ROOT / "bench" / "evidence_corpus.v1.sha256"
METHODS = (
    "direct_rg_or_script",
    "greedy_cli",
    "greedy_mcp_stdio",
    "agent_baseline",
)
GREEDY_METHODS = frozenset({"greedy_cli", "greedy_mcp_stdio"})
FALSE_CHEAP_FAMILY = "false-cheap-edit"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_version() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        pyproject.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if match:
        return match.group(1)
    try:
        return importlib.metadata.version("greedy-token")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _read_lock(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip().split()[0]


def _unknown_metric(unit: str, reason: str) -> dict[str, Any]:
    return {
        "value": None,
        "unit": unit,
        "status": "unknown",
        "authoritative": False,
        "source": None,
        "reason": reason,
    }


def _zero_metric(unit: str, source: str) -> dict[str, Any]:
    return {
        "value": 0,
        "unit": unit,
        "status": "measured",
        "authoritative": True,
        "source": source,
        "reason": None,
    }


def _normalize_authoritative_metric(
    raw: object,
    *,
    unit: str,
    unknown_reason: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _unknown_metric(unit, unknown_reason)
    value = raw.get("value")
    source = str(raw.get("source") or "").strip()
    authoritative = raw.get("authoritative") is True
    if (
        authoritative
        and source
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return {
            "value": value,
            "unit": unit,
            "status": "measured",
            "authoritative": True,
            "source": source,
            "reason": None,
        }
    return _unknown_metric(unit, unknown_reason)


def _load_corpus(path: Path, lock_path: Path) -> tuple[dict, dict]:
    digest = _sha256(path)
    expected = _read_lock(lock_path)
    lock = {
        "path": path.name,
        "sha256": digest,
        "expected_sha256": expected or None,
        "verified": bool(expected) and digest == expected,
    }
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("evidence corpus must be a YAML object")
    corpus = data.get("corpus") or {}
    if corpus.get("status") != "frozen":
        raise ValueError("evidence corpus must declare status: frozen")
    if set(corpus.get("languages") or []) != {"en", "ru"}:
        raise ValueError("evidence corpus must declare both EN and RU")
    if not lock["verified"]:
        raise ValueError(
            f"frozen corpus lock mismatch: expected={expected or 'missing'} actual={digest}"
        )
    return data, lock


def _write_fixture(corpus: dict, root: Path) -> None:
    fixture = corpus["fixture"]
    for rel in fixture.get("directories") or []:
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel, content in (fixture.get("files") or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")
        if rel.startswith("scripts/"):
            path.chmod(0o755)

    routes_source = REPO_ROOT / fixture["route_config_source"]
    routes_target = root / "workspace-routes.yaml"
    shutil.copyfile(routes_source, routes_target)
    (root / ".greedy-token.yaml").write_text(
        "routes_file: workspace-routes.yaml\n"
        "cheap_llm:\n"
        "  provider: ollama\n"
        f"  url: {os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')}\n"
        f"  model: {os.environ.get('OLLAMA_MODEL', 'evidence-stub')}\n",
        encoding="utf-8",
    )
    invalidate_rag_index(root)


class _OllamaStubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("/api/tags", "/v1/models"):
            self.send_error(404)
            return
        self._json(
            {
                "models": [
                    {
                        "name": "evidence-stub",
                        "model": "evidence-stub",
                    }
                ],
                "data": [{"id": "evidence-stub"}],
            }
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in (
            "/api/chat",
            "/v1/chat/completions",
        ):
            self.send_error(404)
            return
        self._json(
            {
                "message": {"role": "assistant", "content": "EVIDENCE_OK"},
                "choices": [
                    {"message": {"role": "assistant", "content": "EVIDENCE_OK"}}
                ],
                "prompt_eval_count": 3,
                "eval_count": 1,
            }
        )

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _ollama_stub() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _base_env(root: Path) -> dict[str, str]:
    env = {
        **os.environ,
        "GREEDY_TOKEN_ROOT": str(root),
        "GREEDY_TOKEN_LOG": "0",
        "GREEDY_TOKEN_FOOTER_STYLE": "compact",
        "PYTHONPATH": str(SRC)
        + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
    }
    return env


def _run_process(
    argv: list[str],
    *,
    root: Path,
    input_text: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            env=_base_env(root),
            input=input_text,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return {
            "exit_code": proc.returncode,
            "output": output,
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": None,
        }
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return {
            "exit_code": 124,
            "output": output,
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": f"timeout after {timeout}s",
        }
    except OSError as exc:
        return {
            "exit_code": 126,
            "output": "",
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": str(exc),
        }


def _tool_text(result: Any) -> str:
    blocks = getattr(result, "content", None) or []
    return "\n".join(getattr(block, "text", str(block)) for block in blocks)


async def _mcp_call_async(
    root: Path,
    tool: str,
    arguments: dict[str, Any],
) -> tuple[str, bool]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "greedy_token.mcp"],
        env=_base_env(root),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            return _tool_text(result), not bool(getattr(result, "isError", False))


def _mcp_call(
    root: Path,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        output, ok = asyncio.run(_mcp_call_async(root, tool, arguments))
        return {
            "exit_code": 0 if ok else 1,
            "output": output,
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": None if ok else "MCP tool returned isError",
        }
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return {
            "exit_code": 1,
            "output": "",
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": str(exc),
        }


def _parse_route_target(text: str) -> str:
    match = re.search(r"\bRoute:\s*([A-Za-z]+)", text, flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _observed_escalation(case: dict, target: str, output: str) -> str | None:
    expected = (case.get("oracle") or {}).get("expected_escalation")
    if expected == "tool->rag":
        if "fallback" in output.lower() or "evidence-fallback-playbook" in output:
            return expected
        return None
    if target == "cursor":
        return expected
    return None


def _line_oracle_ok(output: str, expected: dict) -> bool:
    path = str(expected["path"])
    line = int(expected["line"])
    contains = str(expected["contains"])
    return (
        path in output
        and f":{line}:" in output
        and contains in output
    )


def _evaluate(case: dict, raw: dict[str, Any]) -> dict[str, Any]:
    operation = case["operation"]
    oracle = case.get("oracle") or {}
    output = str(raw.get("output") or "")
    exit_code = int(raw.get("exit_code", 1))
    target = str(raw.get("route_target") or "")
    checks: dict[str, bool] = {}

    if "expected_exit_code" in oracle:
        checks["exit_code"] = exit_code == int(oracle["expected_exit_code"])
    if oracle.get("output_contains"):
        checks["output"] = all(
            str(value) in output for value in oracle["output_contains"]
        )
    if oracle.get("expected_files"):
        checks["files"] = all(
            str(path) in output for path in oracle["expected_files"]
        )
    if oracle.get("expected_lines"):
        checks["lines"] = all(
            _line_oracle_ok(output, expected)
            for expected in oracle["expected_lines"]
        )
    if oracle.get("expected_chunk_ids"):
        checks["chunks"] = all(
            str(chunk_id) in output for chunk_id in oracle["expected_chunk_ids"]
        )
    if operation in ("escalation", "route-only"):
        checks["target"] = target == case["expected_target"]
    expected_escalation = oracle.get("expected_escalation")
    observed_escalation = _observed_escalation(case, target, output)
    if expected_escalation:
        checks["escalation"] = observed_escalation == expected_escalation
    if not checks:
        checks["completed"] = exit_code == 0

    return {
        **raw,
        "success": all(checks.values()) and raw.get("error") is None,
        "checks": checks,
        "route_target": target or None,
        "observed_escalation": observed_escalation,
    }


def _not_applicable(case: dict, method: str, reason: str) -> dict[str, Any]:
    return {
        "case_id": case["id"],
        "method": method,
        "evidence_level": "measured",
        "applicable": False,
        "success": None,
        "checks": {},
        "exit_code": None,
        "output_excerpt": "",
        "error": None,
        "reason": reason,
        "duration_ms": None,
        "attempts": 0,
        "retries": 0,
        "escalations": [],
        "llm_tokens": _unknown_metric("tokens", "method not applicable"),
        "actual_cost_usd": _unknown_metric("USD", "method not applicable"),
        "cursor_cost_usd": _unknown_metric(
            "USD", "Cursor billing unavailable"
        ),
        "savings": {
            "eligible": False,
            "status": "not_applicable",
            "tokens_saved": None,
            "cost_saved_usd": None,
        },
    }


def _billing_for_method(method: str, evidence_level: str) -> tuple[dict, dict, dict]:
    if evidence_level == "contract_stub":
        return (
            _unknown_metric("tokens", "agent contract stub has no token usage"),
            _unknown_metric("USD", "agent contract stub has no billing"),
            _unknown_metric("USD", "Cursor billing unavailable"),
        )
    source = (
        "no model call (direct/CLI executor)"
        if method != "greedy_mcp_stdio"
        else "MCP server executor made no model call"
    )
    return (
        _zero_metric("tokens", source),
        _zero_metric("USD", source),
        _unknown_metric(
            "USD",
            "Cursor/agent-host billing is outside the protocol benchmark",
        ),
    )


def _finalize_observation(
    case: dict,
    method: str,
    raw: dict[str, Any],
    *,
    evidence_level: str = "measured",
    attempts: int = 1,
    retries: int = 0,
    escalations: list[str] | None = None,
) -> dict[str, Any]:
    evaluated = _evaluate(case, raw)
    llm_tokens, actual_cost, cursor_cost = _billing_for_method(
        method, evidence_level
    )
    success = bool(evaluated["success"])
    cheap_success = (
        success
        and evidence_level == "measured"
        and method != "agent_baseline"
        and case["expected_target"] != "cursor"
    )
    if not success:
        savings_status = "excluded_task_failed"
    elif not cheap_success:
        savings_status = "excluded_non_measured_or_cursor"
    else:
        savings_status = "unknown_no_authoritative_agent_baseline"
    return {
        "case_id": case["id"],
        "method": method,
        "evidence_level": evidence_level,
        "applicable": True,
        "operation": case["operation"],
        "layer": (
            "executor"
            if case["operation"] in ("search", "script")
            else (
                "retrieval"
                if case["operation"] in ("rag", "fallback")
                else "escalation"
            )
        ),
        "success": success,
        "checks": evaluated["checks"],
        "exit_code": evaluated.get("exit_code"),
        "route_target": evaluated.get("route_target"),
        "observed_escalation": evaluated.get("observed_escalation"),
        "output_excerpt": str(evaluated.get("output") or "")[:1200],
        "error": evaluated.get("error"),
        "duration_ms": round(float(evaluated["duration_ms"]), 3),
        "attempts": attempts,
        "retries": retries,
        "escalations": list(escalations or []),
        "llm_tokens": llm_tokens,
        "actual_cost_usd": actual_cost,
        "cursor_cost_usd": cursor_cost,
        "savings": {
            "eligible": cheap_success,
            "status": savings_status,
            "tokens_saved": None,
            "cost_saved_usd": None,
        },
    }


def _run_direct(case: dict, root: Path) -> dict[str, Any]:
    operation = case["operation"]
    if operation in ("search", "fallback"):
        rg = shutil.which("rg")
        if not rg:
            raw = {
                "exit_code": 127,
                "output": "",
                "duration_ms": 0.0,
                "error": "ripgrep not installed",
            }
        else:
            raw = _run_process(
                [
                    rg,
                    "-n",
                    "--max-columns",
                    "200",
                    "-F",
                    case["query"],
                    case["scope"],
                ],
                root=root,
            )
        return _finalize_observation(case, METHODS[0], raw)
    if operation == "script":
        raw = _run_process(list(case["direct_argv"]), root=root)
        return _finalize_observation(case, METHODS[0], raw)
    return _not_applicable(
        case,
        METHODS[0],
        "direct baseline is intentionally limited to rg and scripts",
    )


def _run_cli(case: dict, root: Path) -> dict[str, Any]:
    operation = case["operation"]
    prefix = [sys.executable, "-m", "greedy_token", "--no-log"]
    attempts = 1
    escalations: list[str] = []
    if operation in ("search", "script", "fallback"):
        argv = [*prefix, "run", case["task"], "--execute"]
        if operation == "fallback":
            attempts = 2
            escalations = ["tool->rag"]
    elif operation == "rag":
        argv = [*prefix, "rag", case["query"], "--domain", case["domain"]]
    else:
        argv = [*prefix, "route", case["task"]]
    raw = _run_process(argv, root=root)
    if operation in ("escalation", "route-only"):
        raw["route_target"] = _parse_route_target(raw["output"])
    return _finalize_observation(
        case,
        "greedy_cli",
        raw,
        attempts=attempts,
        escalations=escalations,
    )


def _run_mcp(case: dict, root: Path) -> dict[str, Any]:
    operation = case["operation"]
    attempts = 1
    escalations: list[str] = []
    if operation == "search":
        tool = "greedy_token_search"
        arguments = {
            "query": case["query"],
            "path": case["scope"],
            "context": "none",
        }
    elif operation == "script":
        tool = "greedy_token_pipeline"
        arguments = {"task": case["mcp_pipeline"], "execute": True}
    elif operation == "rag":
        tool = "greedy_token_rag"
        arguments = {"query": case["query"], "domain": case["domain"]}
    elif operation == "fallback":
        tool = "greedy_token_pipeline"
        arguments = {"task": case["mcp_pipeline"], "execute": True}
        attempts = 2
        escalations = ["tool->rag"]
    else:
        tool = "greedy_token_route"
        arguments = {"task": case["task"]}
    raw = _mcp_call(root, tool, arguments)
    if operation in ("escalation", "route-only"):
        raw["route_target"] = _parse_route_target(raw["output"])
    return _finalize_observation(
        case,
        "greedy_mcp_stdio",
        raw,
        attempts=attempts,
        escalations=escalations,
    )


def _agent_stub(case: dict) -> dict[str, Any]:
    """Contract-only baseline. It is excluded from evidence and savings gates."""
    oracle = case.get("oracle") or {}
    started = time.perf_counter_ns()
    fragments: list[str] = []
    for expected in oracle.get("expected_lines") or []:
        fragments.append(
            f"{expected['path']}:{expected['line']}:{expected['contains']}"
        )
    fragments.extend(str(value) for value in oracle.get("output_contains") or [])
    fragments.extend(str(value) for value in oracle.get("expected_chunk_ids") or [])
    raw = {
        "exit_code": int(oracle.get("expected_exit_code", 0)),
        "output": "\n".join(fragments),
        "route_target": case["expected_target"],
        "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "error": None,
    }
    attempts = 2 if case["operation"] == "fallback" else 1
    escalations = (
        [oracle["expected_escalation"]]
        if oracle.get("expected_escalation")
        else []
    )
    return _finalize_observation(
        case,
        "agent_baseline",
        raw,
        evidence_level="contract_stub",
        attempts=attempts,
        escalations=escalations,
    )


def _run_host_adapter(
    case: dict,
    root: Path,
    command: str,
) -> dict[str, Any]:
    request = {
        "schema_version": 1,
        "case_id": case["id"],
        "task": case["task"],
        "operation": case["operation"],
        "workspace": str(root),
    }
    raw_proc = _run_process(
        shlex.split(command),
        root=root,
        input_text=json.dumps(request),
        timeout=300.0,
    )
    if raw_proc["exit_code"] != 0:
        return _finalize_observation(
            case,
            "agent_baseline",
            raw_proc,
            evidence_level="live_host",
        )
    try:
        payload = json.loads(raw_proc["output"])
    except json.JSONDecodeError as exc:
        raw_proc["exit_code"] = 1
        raw_proc["error"] = f"host adapter returned invalid JSON: {exc}"
        return _finalize_observation(
            case,
            "agent_baseline",
            raw_proc,
            evidence_level="live_host",
        )

    raw = {
        "exit_code": int(payload.get("exit_code", 0)),
        "output": str(payload.get("output") or ""),
        "route_target": str(payload.get("route_target") or ""),
        "duration_ms": raw_proc["duration_ms"],
        "error": payload.get("error"),
    }
    attempts = max(1, int(payload.get("attempts", 1)))
    retries = max(0, min(int(payload.get("retries", 0)), attempts - 1))
    escalations = [str(value) for value in payload.get("escalations") or []]
    observed = _finalize_observation(
        case,
        "agent_baseline",
        raw,
        evidence_level="live_host",
        attempts=attempts,
        retries=retries,
        escalations=escalations,
    )
    observed["llm_tokens"] = _normalize_authoritative_metric(
        payload.get("llm_tokens"),
        unit="tokens",
        unknown_reason="host adapter supplied no authoritative token usage",
    )
    observed["actual_cost_usd"] = _normalize_authoritative_metric(
        payload.get("actual_cost_usd"),
        unit="USD",
        unknown_reason="host adapter supplied no authoritative billing",
    )
    observed["cursor_cost_usd"] = _normalize_authoritative_metric(
        payload.get("cursor_cost_usd"),
        unit="USD",
        unknown_reason="Cursor cost unavailable from host adapter",
    )
    return observed


def _classify_routes(cases: list[dict], root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous_log = os.environ.get("GREEDY_TOKEN_LOG")
    os.environ["GREEDY_TOKEN_LOG"] = "0"
    try:
        for case in cases:
            started = time.perf_counter_ns()
            decision = route_task(case["task"], root)
            duration_ms = (time.perf_counter_ns() - started) / 1_000_000
            actual = decision.target
            expected = case["expected_target"]
            rows.append(
                {
                    "case_id": case["id"],
                    "lang": case["lang"],
                    "family": case["family"],
                    "expected_target": expected,
                    "actual_target": actual,
                    "route_id": decision.route_id,
                    "ok": actual == expected,
                    "false_cheap": (
                        case["family"] == FALSE_CHEAP_FAMILY
                        and actual != "cursor"
                    ),
                    "duration_ms": round(duration_ms, 3),
                }
            )
    finally:
        if previous_log is None:
            os.environ.pop("GREEDY_TOKEN_LOG", None)
        else:
            os.environ["GREEDY_TOKEN_LOG"] = previous_log
    return rows


def _nearest_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _rate(successes: int, total: int) -> float | None:
    return round(successes / total, 4) if total else None


def _method_summary(observations: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for method in METHODS:
        rows = [
            row
            for row in observations
            if row["method"] == method and row["applicable"]
        ]
        measured = [row for row in rows if row["evidence_level"] != "contract_stub"]
        successes = sum(row["success"] is True for row in rows)
        measured_successes = sum(row["success"] is True for row in measured)
        durations = [float(row["duration_ms"]) for row in measured]
        stub_rows = [
            row for row in rows if row["evidence_level"] == "contract_stub"
        ]
        result[method] = {
            "applicable_runs": len(rows),
            "measured_runs": len(measured),
            "successes": measured_successes,
            "task_success_rate": _rate(measured_successes, len(measured)),
            "p50_ms": _nearest_percentile(durations, 0.50),
            "p95_ms": _nearest_percentile(durations, 0.95),
            "contract_stub": {
                "runs": len(stub_rows),
                "contract_successes": successes - measured_successes,
                "contract_success_rate": _rate(
                    successes - measured_successes,
                    len(stub_rows),
                ),
                "not_agent_evidence": bool(stub_rows),
            },
            "evidence_level": sorted(
                {str(row["evidence_level"]) for row in rows}
            ),
        }
    return result


def _layer_summary(observations: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for layer in ("executor", "retrieval", "escalation"):
        rows = [
            row
            for row in observations
            if row.get("layer") == layer
            and row["applicable"]
            and row["evidence_level"] != "contract_stub"
        ]
        successes = sum(row["success"] is True for row in rows)
        result[layer] = {
            "runs": len(rows),
            "successes": successes,
            "success_rate": _rate(successes, len(rows)),
            "by_method": {
                method: {
                    "runs": len(method_rows),
                    "successes": sum(
                        row["success"] is True for row in method_rows
                    ),
                    "success_rate": _rate(
                        sum(row["success"] is True for row in method_rows),
                        len(method_rows),
                    ),
                }
                for method in METHODS
                if (
                    method_rows := [
                        row for row in rows if row["method"] == method
                    ]
                )
            },
        }
    return result


def _billing_summary(observations: list[dict]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for method in METHODS:
        rows = [
            row
            for row in observations
            if row["method"] == method and row["applicable"]
        ]
        tokens = [row["llm_tokens"] for row in rows]
        costs = [row["actual_cost_usd"] for row in rows]
        cursor_costs = [row["cursor_cost_usd"] for row in rows]

        def aggregate(metrics: list[dict], unit: str, reason: str) -> dict:
            if metrics and all(metric["authoritative"] for metric in metrics):
                return {
                    "value": round(
                        sum(float(metric["value"]) for metric in metrics),
                        6,
                    ),
                    "unit": unit,
                    "status": "measured",
                    "authoritative": True,
                    "sources": sorted(
                        {str(metric["source"]) for metric in metrics}
                    ),
                }
            return _unknown_metric(unit, reason)

        summary[method] = {
            "llm_tokens": aggregate(
                tokens,
                "tokens",
                "not every run has authoritative token usage",
            ),
            "actual_cost_usd": aggregate(
                costs,
                "USD",
                "not every run has authoritative billing",
            ),
            "cursor_cost_usd": aggregate(
                cursor_costs,
                "USD",
                "Cursor billing data unavailable",
            ),
        }
    return summary


def _apply_authoritative_savings(observations: list[dict]) -> None:
    """Compare only successful same-case runs with authoritative host data."""
    baselines = {
        (row["case_id"], int(row.get("repetition", 1))): row
        for row in observations
        if row["method"] == "agent_baseline"
        and row["applicable"]
        and row["success"] is True
        and row["evidence_level"] == "live_host"
    }
    for row in observations:
        savings = row["savings"]
        if not savings["eligible"]:
            continue
        baseline = baselines.get(
            (row["case_id"], int(row.get("repetition", 1)))
        )
        if baseline is None:
            continue
        measured: list[str] = []
        row_tokens = row["llm_tokens"]
        baseline_tokens = baseline["llm_tokens"]
        if row_tokens["authoritative"] and baseline_tokens["authoritative"]:
            savings["tokens_saved"] = (
                float(baseline_tokens["value"]) - float(row_tokens["value"])
            )
            measured.append("tokens")
        row_cost = row["actual_cost_usd"]
        baseline_cost = baseline["actual_cost_usd"]
        if row_cost["authoritative"] and baseline_cost["authoritative"]:
            savings["cost_saved_usd"] = round(
                float(baseline_cost["value"]) - float(row_cost["value"]),
                6,
            )
            measured.append("cost")
        if measured:
            savings["status"] = "measured_authoritative_same_task_baseline"
            savings["metrics"] = measured


def _savings_summary(observations: list[dict]) -> dict[str, Any]:
    eligible = [
        row for row in observations if row["savings"]["eligible"]
    ]
    token_rows = [
        row
        for row in eligible
        if row["savings"]["tokens_saved"] is not None
    ]
    cost_rows = [
        row
        for row in eligible
        if row["savings"]["cost_saved_usd"] is not None
    ]
    if not token_rows and not cost_rows:
        status = "unknown_no_authoritative_same-task_agent_baseline"
    elif len(token_rows) == len(eligible) and len(cost_rows) == len(eligible):
        status = "measured"
    else:
        status = "partial_authoritative_coverage"
    return {
        "eligible_successful_runs": len(eligible),
        "measured_token_comparisons": len(token_rows),
        "measured_cost_comparisons": len(cost_rows),
        "measured_tokens_saved": (
            round(
                sum(float(row["savings"]["tokens_saved"]) for row in token_rows),
                3,
            )
            if token_rows
            else None
        ),
        "measured_cost_saved_usd": (
            round(
                sum(
                    float(row["savings"]["cost_saved_usd"])
                    for row in cost_rows
                ),
                6,
            )
            if cost_rows
            else None
        ),
        "status": status,
        "failed_runs_excluded": sum(
            row["applicable"] and row["success"] is False
            for row in observations
        ),
    }


def _live_ollama_probe(url: str, model: str) -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        with urllib.request.urlopen(
            f"{url.rstrip('/')}/api/tags",
            timeout=5,
        ) as response:
            tags = json.loads(response.read().decode("utf-8"))
        request = urllib.request.Request(
            f"{url.rstrip('/')}/api/chat",
            data=json.dumps(
                {
                    "model": model,
                    "stream": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Reply exactly EVIDENCE_OK",
                        }
                    ],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = str((payload.get("message") or {}).get("content") or "")
        prompt_tokens = payload.get("prompt_eval_count")
        eval_tokens = payload.get("eval_count")
        if isinstance(prompt_tokens, int) and isinstance(eval_tokens, int):
            tokens = {
                "value": prompt_tokens + eval_tokens,
                "unit": "tokens",
                "status": "measured",
                "authoritative": True,
                "source": "Ollama prompt_eval_count + eval_count",
                "reason": None,
            }
        else:
            tokens = _unknown_metric(
                "tokens",
                "Ollama response omitted token counters",
            )
        return {
            "status": "passed" if "EVIDENCE_OK" in text else "failed",
            "model": model,
            "models_visible": len(tags.get("models") or []),
            "duration_ms": round(
                (time.perf_counter_ns() - started) / 1_000_000,
                3,
            ),
            "llm_tokens": tokens,
            "actual_cost_usd": _unknown_metric(
                "USD",
                "Ollama exposes no authoritative monetary billing",
            ),
        }
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {
            "status": "failed",
            "model": model,
            "duration_ms": round(
                (time.perf_counter_ns() - started) / 1_000_000,
                3,
            ),
            "error": str(exc),
            "llm_tokens": _unknown_metric("tokens", "probe failed"),
            "actual_cost_usd": _unknown_metric("USD", "probe failed"),
        }


async def _mcp_list_tools_async(root: Path) -> list[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "greedy_token.mcp"],
        env=_base_env(root),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [tool.name for tool in result.tools]


def _live_mcp_probe(root: Path) -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        names = asyncio.run(_mcp_list_tools_async(root))
        return {
            "status": "passed",
            "transport": "stdio",
            "tools": names,
            "duration_ms": round(
                (time.perf_counter_ns() - started) / 1_000_000,
                3,
            ),
        }
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return {
            "status": "failed",
            "transport": "stdio",
            "error": str(exc),
            "duration_ms": round(
                (time.perf_counter_ns() - started) / 1_000_000,
                3,
            ),
        }


def _build_scorecard(
    *,
    corpus: dict,
    lock: dict,
    mode: str,
    repetitions: int,
    route_rows: list[dict],
    observations: list[dict],
    live_probes: dict[str, Any],
    allow_metered_api: bool,
) -> dict[str, Any]:
    _apply_authoritative_savings(observations)
    route_hits = sum(row["ok"] for row in route_rows)
    false_cheap_rows = [
        row for row in route_rows if row["family"] == FALSE_CHEAP_FAMILY
    ]
    false_cheap = sum(row["false_cheap"] for row in false_cheap_rows)
    route_accuracy = _rate(route_hits, len(route_rows)) or 0.0
    false_cheap_rate = _rate(false_cheap, len(false_cheap_rows)) or 0.0
    methods = _method_summary(observations)
    layers = _layer_summary(observations)
    billing = _billing_summary(observations)
    thresholds = corpus["thresholds"]

    def greedy_layer_rate(layer: str) -> float:
        rows = [
            row
            for row in observations
            if row.get("layer") == layer
            and row["method"] in GREEDY_METHODS
            and row["applicable"]
        ]
        return _rate(
            sum(row["success"] is True for row in rows),
            len(rows),
        ) or 0.0

    failures = [
        row
        for row in observations
        if row["applicable"] and row["success"] is False
    ]
    failed_excluded = all(
        row["savings"]["eligible"] is False
        and row["savings"]["tokens_saved"] is None
        and row["savings"]["cost_saved_usd"] is None
        for row in failures
    )
    retry_attempts = sum(int(row["attempts"]) for row in observations)
    retries = sum(int(row["retries"]) for row in observations)
    escalations = sum(len(row["escalations"]) for row in observations)

    gates = {
        "corpus_lock_verified": lock["verified"],
        "route_and_task_success_separate": True,
        "route_accuracy": route_accuracy
        >= float(thresholds["route_accuracy_min"]),
        "false_cheap_rate_zero": false_cheap_rate
        == float(thresholds["false_cheap_rate"])
        == 0.0,
        "greedy_executor_success": greedy_layer_rate("executor")
        >= float(thresholds["greedy_executor_success_min"]),
        "greedy_retrieval_success": greedy_layer_rate("retrieval")
        >= float(thresholds["greedy_retrieval_success_min"]),
        "greedy_cursor_escalation": greedy_layer_rate("escalation")
        >= float(thresholds["greedy_cursor_escalation_min"]),
        "failed_work_excluded_from_savings": failed_excluded,
        "retries_and_escalations_counted": all(
            row["duration_ms"] is not None
            for row in observations
            if row["applicable"] and row["attempts"] > 1
        ),
        "metered_api_default_denied": True,
        "cursor_cost_never_estimated_as_measured": all(
            entry["cursor_cost_usd"]["status"] == "unknown"
            or entry["cursor_cost_usd"]["authoritative"] is True
            for entry in billing.values()
        ),
        "no_unmeasured_savings_claim": all(
            (
                row["savings"]["tokens_saved"] is None
                and row["savings"]["cost_saved_usd"] is None
            )
            or row["savings"]["status"]
            == "measured_authoritative_same_task_baseline"
            for row in observations
        ),
    }
    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "benchmark": {
            "id": corpus["corpus"]["id"],
            "corpus_version": corpus["corpus"]["version"],
            "mode": mode,
            "repetitions": repetitions,
            "corpus_lock": lock,
            "implementation": {
                "greedy_token_version": _package_version(),
                "source_commit": os.environ.get("GITHUB_SHA") or None,
                "route_config": corpus["fixture"]["route_config_source"],
                "route_config_sha256": _sha256(
                    REPO_ROOT / corpus["fixture"]["route_config_source"]
                ),
            },
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "ripgrep": shutil.which("rg") or None,
            },
            "billing_policy": {
                "metered_api_allowed": allow_metered_api,
                "default": "deny",
                "rule": (
                    "Tokens and USD are measured only from authoritative "
                    "runtime/provider data; otherwise null/unknown."
                ),
            },
        },
        "summary": {
            "routing": {
                "metric_scope": "route classification only",
                "hits": route_hits,
                "n": len(route_rows),
                "accuracy": route_accuracy,
                "false_cheap_n": false_cheap,
                "false_cheap_rate": false_cheap_rate,
            },
            "task_success": {
                "metric_scope": "oracle-verified executor/retrieval/escalation outcomes",
                "by_method": methods,
                "by_layer": layers,
            },
            "attempts": {
                "total_attempts": retry_attempts,
                "retries": retries,
                "escalations": escalations,
                "latency_scope": "wall clock includes all attempts in each run",
            },
            "billing": billing,
            "savings": _savings_summary(observations),
        },
        "gates": {
            **gates,
            "all_passed": all(gates.values()),
        },
        "route_classification": route_rows,
        "observations": observations,
        "live_probes": live_probes,
    }


def _print_summary(scorecard: dict) -> None:
    routing = scorecard["summary"]["routing"]
    methods = scorecard["summary"]["task_success"]["by_method"]
    print(
        "route classification: "
        f"{routing['hits']}/{routing['n']} "
        f"({routing['accuracy']:.1%}); "
        f"false-cheap={routing['false_cheap_rate']:.1%}"
    )
    for method in METHODS:
        row = methods[method]
        rate = row["task_success_rate"]
        rendered = "n/a" if rate is None else f"{rate:.1%}"
        p50 = "n/a" if row["p50_ms"] is None else f"{row['p50_ms']}ms"
        p95 = "n/a" if row["p95_ms"] is None else f"{row['p95_ms']}ms"
        print(
            f"task success {method}: {rendered}; "
            f"p50={p50} p95={p95}"
        )
    print(f"gates: {'PASS' if scorecard['gates']['all_passed'] else 'FAIL'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen public greedy-token evidence benchmark"
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="Frozen versioned corpus YAML",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK,
        help="SHA-256 lock for the frozen corpus",
    )
    parser.add_argument(
        "--mode",
        choices=("deterministic", "live"),
        default="deterministic",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/evidence/scorecard.json"),
    )
    parser.add_argument(
        "--host-command",
        default="",
        help="Manual live adapter: JSON request on stdin, JSON observation on stdout",
    )
    parser.add_argument(
        "--host-billing",
        choices=("unknown", "subscription", "metered"),
        default="unknown",
    )
    parser.add_argument(
        "--allow-metered-api",
        action="store_true",
        help="Explicit opt-in required when --host-billing=metered",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be >= 1")
    if (
        args.host_command
        and args.host_billing == "metered"
        and not args.allow_metered_api
    ):
        raise SystemExit(
            "metered host adapter denied; pass --allow-metered-api explicitly"
        )
    corpus, lock = _load_corpus(args.corpus.resolve(), args.lock.resolve())
    cases = list(corpus.get("cases") or [])
    if not cases:
        raise SystemExit("evidence corpus has no cases")

    live_probes: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="greedy-token-evidence-") as tmp:
        root = Path(tmp)
        if args.mode == "deterministic":
            with _ollama_stub() as stub_url:
                old_url = os.environ.get("OLLAMA_URL")
                old_model = os.environ.get("OLLAMA_MODEL")
                os.environ["OLLAMA_URL"] = stub_url
                os.environ["OLLAMA_MODEL"] = "evidence-stub"
                try:
                    _write_fixture(corpus, root)
                    route_rows = _classify_routes(cases, root)
                    observations = _run_all(
                        cases,
                        root,
                        repetitions=args.repetitions,
                        host_command="",
                        mode=args.mode,
                    )
                finally:
                    if old_url is None:
                        os.environ.pop("OLLAMA_URL", None)
                    else:
                        os.environ["OLLAMA_URL"] = old_url
                    if old_model is None:
                        os.environ.pop("OLLAMA_MODEL", None)
                    else:
                        os.environ["OLLAMA_MODEL"] = old_model
        else:
            _write_fixture(corpus, root)
            route_rows = _classify_routes(cases, root)
            observations = _run_all(
                cases,
                root,
                repetitions=args.repetitions,
                host_command=args.host_command,
                mode=args.mode,
            )
            live_probes["ollama"] = _live_ollama_probe(
                os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M"),
            )
            live_probes["mcp_stdio"] = _live_mcp_probe(root)
            live_probes["agent_host"] = {
                "status": "measured" if args.host_command else "skipped",
                "billing": args.host_billing,
                "cost_rule": (
                    "authoritative adapter value only; otherwise unknown"
                ),
            }

    scorecard = _build_scorecard(
        corpus=corpus,
        lock=lock,
        mode=args.mode,
        repetitions=args.repetitions,
        route_rows=route_rows,
        observations=observations,
        live_probes=live_probes,
        allow_metered_api=args.allow_metered_api,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(scorecard, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _print_summary(scorecard)
    print(f"scorecard: {output}")
    return 0 if scorecard["gates"]["all_passed"] else 1


def _run_all(
    cases: list[dict],
    root: Path,
    *,
    repetitions: int,
    host_command: str,
    mode: str,
) -> list[dict]:
    observations: list[dict] = []
    for repetition in range(1, repetitions + 1):
        for case in cases:
            rows = [
                _run_direct(case, root),
                _run_cli(case, root),
                _run_mcp(case, root),
            ]
            if mode == "live" and host_command:
                rows.append(_run_host_adapter(case, root, host_command))
            elif mode == "deterministic":
                rows.append(_agent_stub(case))
            else:
                rows.append(
                    _not_applicable(
                        case,
                        "agent_baseline",
                        "manual host adapter not configured",
                    )
                )
            for row in rows:
                row["repetition"] = repetition
                observations.append(row)
    return observations


if __name__ == "__main__":
    raise SystemExit(main())
