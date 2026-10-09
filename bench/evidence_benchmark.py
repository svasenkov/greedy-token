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
import sysconfig
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from greedy_token.cheap_llm import (  # noqa: E402
    OBSERVATION_SCOPE,
    OBSERVE_CASE_ENV,
    OBSERVE_HTTP_ALLOW_ENV,
    OBSERVE_LEDGER_ENV,
    OBSERVE_RUN_ENV,
    OBSERVED_EXEC_ENTRY,
    OBSERVED_MODULE_ENTRY,
    observe_ledger_write,
)
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
# Pre-declared per-case completion contract (frozen v1 + P6 extension
# corpus): what terminal evidence a case must produce for its result to
# count as completing the original user task. Echo-contract scripts,
# fallback playbooks and routing/escalation-only answers are separate
# non-completion categories — and an undeclared case fails closed as
# "unclassified".
_COMPLETION_CATEGORY = {
    "tool-search-en": "frozen_search",
    "tool-search-ru": "frozen_search",
    "python-script-en": "contract_only",
    "python-script-ru": "contract_only",
    "rag-retrieval-en": "rag_excerpt",
    "rag-retrieval-ru": "rag_excerpt",
    "tool-to-rag-fallback-en": "fallback_only",
    "false-cheap-edit-en": "routing_only",
    "false-cheap-edit-ru": "routing_only",
    "cursor-design-en": "routing_only",
    "ollama-route-en": "routing_only",
    "ollama-route-ru": "routing_only",
    "p6-search-gamma-en": "frozen_search",
    "p6-search-delta-ru": "frozen_search",
    "p6-search-miss-en": "frozen_search",
    "p6-search-huge-en": "frozen_search",
    "p6-rag-quota-en": "rag_excerpt",
    "p6-rag-quota-ru": "rag_excerpt",
    "p6-fallback-ru": "fallback_only",
    "p6-falsecheap-en": "routing_only",
    "p6-falsecheap-ru": "routing_only",
    "p6-malformed-empty-en": "routing_only",
    "p6-malformed-junk-ru": "routing_only",
    "p6-large-task-ru": "routing_only",
}
_TASK_COMPLETION_CATEGORIES = frozenset({"frozen_search", "rag_excerpt"})
_NON_COMPLETION_DETAIL = {
    "contract_only": (
        "contract-only echo output — the executor ran, but the frozen "
        "sentinel is not the user's task result"
    ),
    "fallback_only": (
        "fallback-only playbook — an escalation artifact, not task "
        "completion"
    ),
    "routing_only": (
        "routing/escalation answer — the original task was handed off"
    ),
    "unclassified": "case has no declared completion contract",
}
# Minimum verbatim run of frozen-document words that counts as a
# substantive RAG excerpt — a bare chunk-id echo is a lookup label, not
# an answer.
_RAG_EXCERPT_MIN_WORDS = 6


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


def _bench_model(default: str) -> str:
    """Model name for the benchmark run.

    BENCH_MODEL wins; OLLAMA_MODEL stays a read-only legacy alias for manual
    runs (never written back into os.environ — that would trip greedy-token's
    deprecation warning inside this process).
    """
    return (
        os.environ.get("BENCH_MODEL", "").strip()
        or os.environ.get("OLLAMA_MODEL", "").strip()
        or default
    )


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
        f"  model: {_bench_model('evidence-stub')}\n",
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
        # The stub answers availability probes only — a model POST must never
        # masquerade as a completed inference.
        self.send_error(
            403,
            "model dispatch is not part of the availability stub",
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
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            env=env if env is not None else _base_env(root),
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


# ---------------------------------------------------------------------------
# D1 observed-run driver.
#
# Every product child in deterministic mode runs under the coverage-aware
# observer: an independent JSONL ledger (never stdout/footer/usage/spend)
# bootstrapped by the driver before the product import chain starts. The
# ledger covers the python_provider scope only — in-process model intents,
# pre-network dispatch denials, denied opaque native launches, provider
# probes and guarded HTTP IO. Whole-invocation counters are only reported
# when coverage closed cleanly; any gap degrades them to unknown, never zero.
# ---------------------------------------------------------------------------

_CODE_WATCH = (
    "src/greedy_token/cheap_llm.py",
    "src/greedy_token/expensive_llm.py",
    "src/greedy_token/llm_invoke.py",
    "src/greedy_token/router.py",
    "src/greedy_token/cli.py",
    "src/greedy_token/mcp.py",
    "src/greedy_token/executors.py",
    "src/greedy_token/pipeline.py",
    "src/greedy_token/result_gate.py",
    "bench/evidence_benchmark.py",
)
_OBSERVED_TIMEOUT_S = 60.0
_observed_seq = 0


def _code_sha256() -> str:
    digest = hashlib.sha256()
    for rel in _CODE_WATCH:
        digest.update(rel.encode())
        digest.update(b"\x00")
        digest.update(_sha256(REPO_ROOT / rel).encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def _observe_driver_emit(ledger_path: Path, kind: str, **fields) -> dict:
    global _observed_seq
    _observed_seq += 1
    event = {
        "kind": kind,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "seq": _observed_seq,
        "monotonic": round(time.monotonic(), 6),
        "epoch": round(time.time(), 6),
        **fields,
    }
    observe_ledger_write(str(ledger_path), event)
    return event


def _observe_bootstrap(ledger_path: Path) -> dict:
    """Driver-side bootstrap — precedes every observed child spawn."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    return _observe_driver_emit(
        ledger_path,
        "bootstrap",
        run_id="",
        case_id="",
        channel="independent_jsonl",
        scope=OBSERVATION_SCOPE,
        code_sha256=_code_sha256(),
    )


def _site_packages() -> list[str]:
    """site-packages dirs the `-S` child cannot discover on its own."""
    paths = sysconfig.get_paths()
    return sorted(
        {paths[key] for key in ("purelib", "platlib") if paths.get(key)}
    )


def _observed_child_env(
    root: Path,
    *,
    run_id: str,
    case_id: str,
    ledger_path: Path,
) -> dict[str, str]:
    """Hermetic environment for the test driver — allowlist, not os.environ."""
    ollama_url = os.environ.get(
        "OLLAMA_URL", "http://127.0.0.1:11434"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root / "_observed_home"),
        "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
        # `-S` skips site.py/user-site entirely, so the first Python code
        # inside the child is the observer bootstrap — not site imports.
        "PYTHONPATH": os.pathsep.join(
            [str(SRC), str(REPO_ROOT), *_site_packages()]
        ),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "GREEDY_TOKEN_ROOT": str(root),
        "GREEDY_TOKEN_LOG": "0",
        "GREEDY_TOKEN_FOOTER_STYLE": "compact",
        "GREEDY_TOKEN_HOME": str(root / "_observed_gt_home"),
        "OLLAMA_URL": ollama_url,
        "BENCH_MODEL": os.environ.get("BENCH_MODEL", "evidence-stub"),
        OBSERVE_LEDGER_ENV: str(ledger_path),
        OBSERVE_RUN_ENV: run_id,
        OBSERVE_CASE_ENV: case_id,
        OBSERVE_HTTP_ALLOW_ENV: ollama_url,
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR"):
            value = os.environ.get(name)
            if value:
                env[name] = value
    return env


def _env_sha256(env: dict[str, str]) -> str:
    blob = "\x00".join(f"{key}={env[key]}" for key in sorted(env))
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def _run_input_sha256(
    case: dict | None,
    method: str,
    payload: Any,
) -> str:
    blob = json.dumps(
        {
            "case_id": (case or {}).get("id", ""),
            "method": method,
            "payload": payload,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_ledger(ledger_path: Path) -> list[dict]:
    """Every ledger line, unfiltered — run attribution happens in the report."""
    if not ledger_path.is_file():
        return []
    events: list[dict] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {"kind": "ledger_corrupt", "run_id": ""}
        events.append(event)
    return events


def _events_sha256(events: list[dict]) -> str:
    blob = json.dumps(events, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _unknown_count(scope: str = "invocation") -> dict[str, Any]:
    return {"value": None, "status": "unknown", "scope": scope}


def _coverage_observed_report(
    ledger_events: list[dict],
    *,
    timed_out: bool,
    exit_code: int | None,
    run_id: str,
    case_id: str | None = None,
    expected: dict[str, str] | None = None,
    ledger_file: Path | None = None,
) -> dict[str, Any]:
    """Coverage verdict for one observed run.

    complete  → bootstrap + run boundary + every supported-scope event
                closed cleanly, in order, on the shared deadline — and
                nothing opaque was requested or denied.
    incomplete→ a supported-scope gap or ledger-integrity violation
                exists (denied native launch, sequence loss/prefix,
                foreign run events, missing closure, corrupt lines,
                denied IO, boundary mismatch, deadline breach).
    unobserved→ no child segment at all (bootstrap/install never ran).
    """
    reasons: list[str] = []

    bootstrap = [
        e for e in ledger_events if e.get("kind") == "bootstrap"
    ]
    corrupt = [
        e for e in ledger_events if e.get("kind") == "ledger_corrupt"
    ]
    foreign = [
        e
        for e in ledger_events
        if e.get("kind") not in ("bootstrap", "ledger_corrupt")
        and e.get("run_id") not in ("", run_id)
    ]
    # An execution/model/IO event with no run attribution is an integrity
    # violation — filtering it out of the per-run view must not erase the
    # gap. Only the driver bootstrap may be run-global.
    unattributed = [
        e
        for e in ledger_events
        if e.get("kind") not in ("bootstrap", "ledger_corrupt")
        and not e.get("run_id")
    ]
    events = [
        e for e in ledger_events if e.get("run_id") == run_id
    ]

    def count(kind: str, **match) -> int:
        return sum(
            1
            for e in events
            if e.get("kind") == kind
            and all(e.get(key) == value for key, value in match.items())
        )

    model_attempts = count("model_attempt")
    llm_denied = count("llm_request", decision="denied")
    llm_sent = count("llm_request", decision="sent")
    native_denied = count("native_launch", decision="denied")
    native_allowed = count("native_launch", decision="allowed")
    native_admitted = count("native_launch", decision="admitted")
    fallbacks = count("model_attempt", cause="provider_fallback")
    probes = count("provider_probe")
    io_denied = count("io_http", decision="denied")
    io_allowed = count("io_http", decision="allowed")

    if not bootstrap:
        reasons.append("missing_bootstrap")
    elif expected and expected.get("code_sha256"):
        if not any(
            e.get("code_sha256") == expected["code_sha256"]
            for e in bootstrap
        ):
            reasons.append("bootstrap_mismatch")
    if corrupt:
        reasons.append("ledger_corrupt")
    if foreign:
        reasons.append("foreign_run_event")
    if unattributed:
        reasons.append("unattributed_event")

    begins = [e for e in events if e.get("kind") == "run_begin"]
    ends = [e for e in events if e.get("kind") == "run_end"]
    begin = begins[0] if begins else None
    deadline_epoch = None
    if len(begins) != 1:
        reasons.append(
            "missing_run_begin" if not begins else "duplicate_run_begin"
        )
    else:
        if expected:
            mismatched = [
                key
                for key in (
                    "input_sha256",
                    "code_sha256",
                    "argv_sha256",
                    "env_sha256",
                )
                if expected.get(key) and begin.get(key) != expected[key]
            ]
            if mismatched:
                reasons.append("run_begin_mismatch")
        raw_deadline = begin.get("deadline_epoch")
        if isinstance(raw_deadline, (int, float)):
            deadline_epoch = float(raw_deadline)
    if len(ends) != 1:
        reasons.append(
            "missing_run_end" if not ends else "duplicate_run_end"
        )

    if case_id is not None:
        if any(
            str(e.get("case_id") or "") not in ("", case_id)
            for e in events
        ):
            reasons.append("case_mismatch")

    # Per-child ordering: events for each started pid must be a contiguous
    # 1..n sequence beginning with child_start and ending with child_exit.
    started_pids = {
        e["pid"] for e in events if e.get("kind") == "child_start"
    }
    exited_pids = {
        e["pid"] for e in events if e.get("kind") == "child_exit"
    }
    boundary_kinds = {"run_begin", "run_end"}
    child_pids = {
        e["pid"]
        for e in events
        if e.get("kind") not in boundary_kinds
    }
    for pid in sorted(child_pids):
        pid_events = [
            e for e in events if e.get("pid") == pid and e.get("kind") not in boundary_kinds
        ]
        if pid not in started_pids:
            reasons.append("events_without_child_start")
            break
        if pid_events[0].get("kind") != "child_start":
            reasons.append("child_event_before_start")
            break
        seqs = [e.get("seq") for e in pid_events]
        if len(set(seqs)) != len(seqs):
            reasons.append("duplicate_event")
            break
        if seqs != list(range(1, len(seqs) + 1)):
            reasons.append("sequence_gap")
            break
        kinds = [e.get("kind") for e in pid_events]
        if "child_exit" in kinds:
            if kinds[-1] != "child_exit":
                reasons.append("event_after_child_exit")
                break
        else:
            reasons.append("missing_child_exit")
            break
    if started_pids - exited_pids:
        if "missing_child_exit" not in reasons:
            reasons.append("missing_child_exit")

    # Trusted-child join: an admitted native launch is verified only when a
    # registered parent admission (argv/env/pass_fds/cwd + measured source,
    # runner and file-identity expectations) is consumed exactly once and
    # the child's own bind event independently reports the same identity.
    # A child self-report alone never completes coverage.
    driver_pid = begin.get("pid") if begin else None
    starts = {
        e["pid"]: e for e in events if e.get("kind") == "child_start"
    }
    positions_of = {id(e): i for i, e in enumerate(events)}
    admissions: dict[str, dict] = {}
    for e in events:
        if e.get("kind") != "trusted_child_admission":
            continue
        aid = str(e.get("admission_id") or "")
        if not aid or aid in admissions:
            reasons.append("duplicate_child_admission")
            break
        admissions[aid] = e
    binds = {
        e["pid"]: e
        for e in events
        if e.get("kind") == "trusted_child_bind"
    }
    admitted_launches: dict[str, dict] = {}
    for e in events:
        if e.get("kind") != "native_launch" or e.get("decision") != "admitted":
            continue
        aid = str(e.get("admission_id") or "")
        if aid not in admissions:
            reasons.append("admitted_launch_without_admission")
            break
        admitted_launches[aid] = e
    primary_pids = {
        pid
        for pid, start in starts.items()
        if driver_pid is not None and start.get("ppid") == driver_pid
    }
    joined: dict[int, str] = {}
    for pid in sorted(started_pids - primary_pids):
        bind = binds.get(pid)
        start = starts.get(pid) or {}
        if bind is None:
            reasons.append("trusted_child_missing_bind")
            break
        aid = str(bind.get("admission_id") or "")
        admission = admissions.get(aid)
        launch = admitted_launches.get(aid)
        if admission is None:
            reasons.append("trusted_child_orphan_bind")
            break
        ordered = (
            launch is not None
            and positions_of[id(admission)] < positions_of[id(launch)]
            < positions_of[id(start)]
            < positions_of[id(bind)]
        )
        if (
            not ordered
            or launch.get("pid") != admission.get("pid")
            or admission.get("pid") != start.get("ppid")
        ):
            reasons.append("trusted_child_spawn_mismatch")
            break
        mismatch = (
            bind.get("source_sha256") != admission.get("source_sha256")
            or bind.get("source_bytes") != admission.get("source_bytes")
            or bind.get("script_path") != admission.get("script_path")
            or bind.get("runner_sha256") != admission.get("runner_sha256")
            or bind.get("env_sha256") != admission.get("env_sha256")
            or bind.get("fd_device") != admission.get("fd_device")
            or bind.get("fd_inode") != admission.get("fd_inode")
            or start.get("argv_sha256") != admission.get("child_argv_sha256")
        )
        if mismatch:
            reasons.append("trusted_child_mismatch")
            break
        joined[pid] = aid
    if len(set(joined.values())) != len(joined):
        reasons.append("trusted_child_replay")
    unjoined = [
        aid for aid in admissions if aid not in set(joined.values())
    ]
    if unjoined:
        reasons.append("trusted_child_unjoined")

    # Boundary order in file position: run_begin before the first child
    # event, run_end after the last one.
    positions = {
        kind: [i for i, e in enumerate(events) if e.get("kind") == kind]
        for kind in ("run_begin", "run_end")
    }
    child_positions = [
        i
        for i, e in enumerate(events)
        if e.get("kind") not in boundary_kinds
    ]
    if child_positions:
        if positions["run_begin"] and positions["run_begin"][0] > child_positions[0]:
            reasons.append("run_begin_after_child")
        if positions["run_end"] and positions["run_end"][-1] < child_positions[-1]:
            reasons.append("run_end_before_child")

    if deadline_epoch is not None:
        slack = 5.0
        if any(
            isinstance(e.get("epoch"), (int, float))
            and float(e["epoch"]) > deadline_epoch + slack
            for e in events
            if e.get("kind") != "run_end"
        ):
            reasons.append("deadline_exceeded")

    # A driver-side spawn failure is 126/127 *with no child segment* — a
    # started child that exits 126 is a product exit, not a spawn error.
    spawn_error = exit_code in (126, 127) and not started_pids
    if timed_out:
        reasons.append("timeout")
    if spawn_error:
        reasons.append("spawn_error")
    if native_denied or native_allowed:
        reasons.append("native_launch_outside_coverage")
    if io_denied:
        reasons.append("io_denied_outside_python_scope")

    # Causal integrity: attempts register unique non-empty ids, causal
    # parents resolve to a different registered attempt, and every model
    # dispatch binds one — an orphan request can never fake a zero-attempt
    # run. An empty attempt id is legitimate only outside model dispatch
    # (e.g. a fixture health probe).
    attempt_ids = [
        str(e.get("attempt_id") or "")
        for e in events
        if e.get("kind") == "model_attempt"
    ]
    attempt_id_set = set(attempt_ids)
    if not all(attempt_ids):
        reasons.append("model_attempt_missing_id")
    if len(attempt_id_set) != len(attempt_ids):
        reasons.append("duplicate_attempt_id")
    for e in events:
        if e.get("kind") != "model_attempt":
            continue
        parent = str(e.get("parent_attempt_id") or "")
        if parent and (
            parent not in attempt_id_set
            or parent == str(e.get("attempt_id") or "")
        ):
            reasons.append("broken_causal_link")
            break
    if any(
        str(e.get("attempt_id") or "") not in attempt_id_set
        for e in events
        if e.get("kind") == "llm_request"
    ):
        reasons.append("orphan_llm_request")
    if any(
        bound not in attempt_id_set
        for e in events
        if e.get("kind") == "io_http"
        for bound in [str(e.get("attempt_id") or "")]
        if bound
    ):
        reasons.append("broken_causal_link")

    if not started_pids:
        status = "unobserved"
        reasons = [
            "no_child_start",
            *[r for r in reasons if r != "missing_child_exit"],
        ]
    elif reasons:
        status = "incomplete"
    else:
        status = "complete"

    complete = status == "complete"
    invocation = {
        "model_attempts": (
            {"value": model_attempts, "status": "observed", "scope": "invocation"}
            if complete
            else _unknown_count()
        ),
        "llm_requests_sent": (
            {"value": llm_sent, "status": "observed", "scope": "invocation"}
            if complete
            else _unknown_count()
        ),
    }
    return {
        "scope": OBSERVATION_SCOPE,
        "run_id": run_id,
        "coverage": {
            "status": status,
            "complete": complete,
            "reasons": reasons,
        },
        "python_scope": {
            "model_attempts": model_attempts,
            "llm_requests_denied": llm_denied,
            "llm_requests_sent": llm_sent,
            "native_launch_denied": native_denied,
            "native_launch_allowed": native_allowed,
            "native_launch_admitted": native_admitted,
            "trusted_children": len(joined),
            "trusted_child_admissions": len(admissions),
            "provider_fallbacks": fallbacks,
            "provider_probes": probes,
            "io_http_denied": io_denied,
            "io_http_allowed": io_allowed,
            "child_pids": sorted(started_pids),
        },
        "invocation": invocation,
        # Attributed counting view — violations outside this run already
        # poisoned coverage above; nothing is filtered away silently.
        "events": events,
        "events_sha256": _events_sha256(events),
        # Full raw validator input for standalone replay: bootstrap,
        # unattributed, foreign and corrupt evidence preserved verbatim,
        # together with the driver-intended expected hashes and the run
        # parameters the verdict depended on.
        "ledger": {
            "events": ledger_events,
            "events_sha256": _events_sha256(ledger_events),
            "file_sha256": (
                _sha256(ledger_file)
                if ledger_file is not None and ledger_file.is_file()
                else None
            ),
            "run_id": run_id,
            "case_id": case_id,
            "timed_out": timed_out,
            "exit_code": exit_code,
            "expected": dict(expected or {}),
        },
    }


def _coverage_report_unobserved(
    *,
    state: str,
    reason: str,
) -> dict[str, Any]:
    status = "not_applicable" if state in ("contract_stub", "not_applicable") else "unobserved"
    return {
        "scope": OBSERVATION_SCOPE,
        "state": state,
        "run_id": None,
        "coverage": {
            "status": status,
            "complete": False,
            "reasons": [reason] if reason else [],
        },
        "python_scope": {
            "model_attempts": 0,
            "llm_requests_denied": 0,
            "llm_requests_sent": 0,
            "native_launch_denied": 0,
            "native_launch_allowed": 0,
            "native_launch_admitted": 0,
            "trusted_children": 0,
            "trusted_child_admissions": 0,
            "provider_fallbacks": 0,
            "provider_probes": 0,
            "io_http_denied": 0,
            "io_http_allowed": 0,
            "child_pids": [],
        },
        "invocation": {
            "model_attempts": _unknown_count(),
            "llm_requests_sent": _unknown_count(),
        },
        "events": [],
        "events_sha256": "",
        "ledger": None,
    }


def _per_run_ledger(ledger_path: Path, run_id: str) -> Path:
    """One ledger file per observed run — a run_id seen nowhere else."""
    return ledger_path.with_name(
        f"{ledger_path.stem}.{run_id}{ledger_path.suffix}"
    )


def _replay_observation(
    observation: dict[str, Any],
) -> dict[str, Any] | None:
    """Re-run the coverage validator using only the stored raw ledger —
    the scorecard row must carry everything the verdict depended on."""
    ledger = observation.get("ledger")
    if not isinstance(ledger, dict):
        return None
    report = _coverage_observed_report(
        ledger.get("events") or [],
        timed_out=bool(ledger.get("timed_out")),
        exit_code=ledger.get("exit_code"),
        run_id=str(ledger.get("run_id") or ""),
        case_id=ledger.get("case_id"),
        expected=ledger.get("expected") or {},
    )
    # Stored artifacts may predate additive scope counters; the stored
    # row defines its own contract. Re-derived keys missing from the
    # report still surface as None and fail the stored comparison.
    stored_scope = observation.get("python_scope")
    if isinstance(stored_scope, dict):
        report["python_scope"] = {
            key: report["python_scope"].get(key) for key in stored_scope
        }
    return report


def _emit_run_begin(
    ledger_path: Path,
    *,
    run_id: str,
    case_id: str,
    method: str,
    input_sha256: str,
    argv_repr: str,
    env: dict[str, str],
    timeout: float,
) -> dict[str, str]:
    """Emit run_begin and return the driver-intended values the report
    must re-verify against the ledger (boundary correspondence)."""
    deadline_epoch = time.time() + timeout
    expected = {
        "input_sha256": input_sha256,
        "code_sha256": _code_sha256(),
        "argv_sha256": _sha256_text(argv_repr),
        "env_sha256": _env_sha256(env),
        "deadline_epoch": f"{deadline_epoch:.3f}",
    }
    _observe_driver_emit(
        ledger_path,
        "run_begin",
        run_id=run_id,
        case_id=case_id,
        method=method,
        input_sha256=input_sha256,
        code_sha256=expected["code_sha256"],
        argv_sha256=expected["argv_sha256"],
        env_keys=sorted(env),
        env_sha256=expected["env_sha256"],
        deadline_epoch=round(deadline_epoch, 3),
        supported_scope=OBSERVATION_SCOPE,
    )
    return expected


def _emit_run_end(
    ledger_path: Path,
    *,
    run_id: str,
    case_id: str,
    raw: dict[str, Any],
    timed_out: bool,
) -> None:
    _observe_driver_emit(
        ledger_path,
        "run_end",
        run_id=run_id,
        case_id=case_id,
        exit_code=raw["exit_code"],
        duration_ms=round(float(raw["duration_ms"]), 3),
        timed_out=timed_out,
    )


def _run_observed(
    argv: list[str],
    *,
    env: dict[str, str],
    root: Path,
    ledger_path: Path,
    run_id: str,
    case_id: str,
    method: str,
    input_sha256: str,
    timeout: float = _OBSERVED_TIMEOUT_S,
    input_text: str | None = None,
) -> dict[str, Any]:
    """Spawn one product child under the observer and close its run."""
    child_ledger = _per_run_ledger(ledger_path, run_id)
    env[OBSERVE_LEDGER_ENV] = str(child_ledger)
    _observe_bootstrap(child_ledger)
    expected = _emit_run_begin(
        child_ledger,
        run_id=run_id,
        case_id=case_id,
        method=method,
        input_sha256=input_sha256,
        argv_repr="\x00".join(argv),
        env=env,
        timeout=timeout,
    )
    raw = _run_process(
        argv,
        root=root,
        input_text=input_text,
        timeout=timeout,
        env=env,
    )
    timed_out = raw["exit_code"] == 124
    _emit_run_end(
        child_ledger,
        run_id=run_id,
        case_id=case_id,
        raw=raw,
        timed_out=timed_out,
    )
    raw["observation"] = _coverage_observed_report(
        _read_ledger(child_ledger),
        timed_out=timed_out,
        exit_code=raw["exit_code"],
        run_id=run_id,
        case_id=case_id,
        expected=expected,
        ledger_file=child_ledger,
    )
    raw["run_id"] = run_id
    return raw


def _new_run_id() -> str:
    return uuid.uuid4().hex


def _run_observed_exec(
    code: str,
    *,
    root: Path,
    ledger_path: Path,
    case_id: str,
    method: str,
    timeout: float = _OBSERVED_TIMEOUT_S,
) -> dict[str, Any]:
    """Calibration driver: in-process provider calls inside an observed child."""
    run_id = _new_run_id()
    env = _observed_child_env(
        root, run_id=run_id, case_id=case_id, ledger_path=ledger_path
    )
    argv = [sys.executable, "-S", "-c", OBSERVED_EXEC_ENTRY, code]
    return _run_observed(
        argv,
        env=env,
        root=root,
        ledger_path=ledger_path,
        run_id=run_id,
        case_id=case_id,
        method=method,
        input_sha256=_run_input_sha256(None, method, {"code_sha256": _sha256_text(code)}),
        timeout=timeout,
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_observed_cli(
    case: dict,
    *,
    root: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    operation = case["operation"]
    if operation in ("search", "script", "fallback"):
        args = ["run", case["task"], "--execute"]
    elif operation == "rag":
        args = ["rag", case["query"], "--domain", case["domain"]]
    else:
        args = ["route", case["task"]]
    run_id = _new_run_id()
    env = _observed_child_env(
        root, run_id=run_id, case_id=case["id"], ledger_path=ledger_path
    )
    argv = [
        sys.executable,
        "-S",
        "-c",
        OBSERVED_MODULE_ENTRY,
        "greedy_token",
        "--no-log",
        *args,
    ]
    raw = _run_observed(
        argv,
        env=env,
        root=root,
        ledger_path=ledger_path,
        run_id=run_id,
        case_id=case["id"],
        method="greedy_cli",
        input_sha256=_run_input_sha256(case, "greedy_cli", args),
    )
    if operation in ("escalation", "route-only"):
        raw["route_target"] = _parse_route_target(raw["output"])
    return _finalize_observation(case, "greedy_cli", raw, root=root)


async def _mcp_call_observed_async(
    root: Path,
    tool: str,
    arguments: dict[str, Any],
    *,
    env: dict[str, str],
    timeout: float,
    cwd: Path | None = None,
    args: list[str] | None = None,
) -> tuple[str, bool]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=args
        or ["-S", "-c", OBSERVED_MODULE_ENTRY, "greedy_token.mcp"],
        env=env,
        cwd=str(cwd or root),
    )
    # One shared deadline for spawn + initialize + call + cleanup — a
    # timeout covering only call_tool would let a hung initialize or a
    # stuck shutdown escape the run's deadline accounting.
    async with asyncio.timeout(timeout):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
                return _tool_text(result), not bool(
                    getattr(result, "isError", False)
                )


def _run_observed_mcp_case(
    case: dict,
    *,
    root: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    operation = case["operation"]
    if operation == "search":
        tool = "greedy_token_search"
        arguments = {
            "query": case["query"],
            "path": case["scope"],
            "context": "none",
        }
    elif operation in ("script", "fallback"):
        tool = "greedy_token_pipeline"
        arguments = {"task": case["mcp_pipeline"], "execute": True}
    elif operation == "rag":
        tool = "greedy_token_rag"
        arguments = {"query": case["query"], "domain": case["domain"]}
    else:
        tool = "greedy_token_route"
        arguments = {"task": case["task"]}

    run_id = _new_run_id()
    env = _observed_child_env(
        root, run_id=run_id, case_id=case["id"], ledger_path=ledger_path
    )
    child_ledger = _per_run_ledger(ledger_path, run_id)
    env[OBSERVE_LEDGER_ENV] = str(child_ledger)
    _observe_bootstrap(child_ledger)
    argv_repr = f"{tool}\x00{json.dumps(arguments, sort_keys=True)}"
    expected = _emit_run_begin(
        child_ledger,
        run_id=run_id,
        case_id=case["id"],
        method="greedy_mcp_stdio",
        input_sha256=_run_input_sha256(case, "greedy_mcp_stdio", arguments),
        argv_repr=argv_repr,
        env=env,
        timeout=_OBSERVED_TIMEOUT_S,
    )
    started = time.perf_counter_ns()
    timed_out = False
    try:
        output, ok = asyncio.run(
            _mcp_call_observed_async(
                root,
                tool,
                arguments,
                env=env,
                timeout=_OBSERVED_TIMEOUT_S,
                cwd=root,
            )
        )
        raw = {
            "exit_code": 0 if ok else 1,
            "output": output,
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": None if ok else "MCP tool returned isError",
        }
    except TimeoutError:
        timed_out = True
        raw = {
            "exit_code": 124,
            "output": "",
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": f"timeout after {_OBSERVED_TIMEOUT_S}s",
        }
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raw = {
            "exit_code": 1,
            "output": "",
            "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "error": str(exc),
        }
    _emit_run_end(
        child_ledger,
        run_id=run_id,
        case_id=case["id"],
        raw=raw,
        timed_out=timed_out,
    )
    raw["observation"] = _coverage_observed_report(
        _read_ledger(child_ledger),
        timed_out=timed_out,
        exit_code=raw["exit_code"],
        run_id=run_id,
        case_id=case["id"],
        expected=expected,
        ledger_file=child_ledger,
    )
    raw["run_id"] = run_id
    if operation in ("escalation", "route-only"):
        raw["route_target"] = _parse_route_target(raw["output"])
    return _finalize_observation(case, "greedy_mcp_stdio", raw, root=root)


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


def _escalation_evidence(case: dict, raw: dict[str, Any]) -> dict[str, Any]:
    required = bool((case.get("oracle") or {}).get("expected_escalation"))
    evidence = {
        "status": "unknown" if required else "not_applicable",
        "observed_transition": None,
        "request_kind": "unknown",
        "source_binding": None,
        "reason": "no verified tier-bound execution/transition source",
    }
    if not required:
        evidence["reason"] = None
        return evidence
    observation = raw.get("observation") or {}
    replayed = _replay_observation(observation)
    ledger = observation.get("ledger") or {}
    if replayed is None or ledger.get("case_id") != case["id"]:
        return evidence
    run_id = ledger.get("run_id")
    if not run_id or run_id != observation.get("run_id") or run_id != raw.get("run_id"):
        return evidence
    expected = ledger.get("expected") or {}
    if not all(expected.get(key) for key in (
        "input_sha256", "code_sha256", "argv_sha256", "env_sha256", "deadline_epoch",
    )) or ledger.get("events_sha256") != _events_sha256(ledger.get("events") or []):
        return evidence
    begin = next((e for e in replayed["events"] if e.get("kind") == "run_begin"), {})
    method = begin.get("method")
    if case["operation"] == "fallback" and method == "greedy_cli":
        payload = ["run", case["task"], "--execute"]
        request_kind = "dynamic_fallback_candidate"
    elif case["operation"] == "fallback" and method == "greedy_mcp_stdio":
        payload = {"task": case["mcp_pipeline"], "execute": True}
        request_kind = "explicit_pipeline"
    elif case["operation"] in ("escalation", "route-only") and method in GREEDY_METHODS:
        payload = ["route", case["task"]] if method == "greedy_cli" else {"task": case["task"]}
        request_kind = "route_only"
    else:
        return evidence
    if expected["input_sha256"] != _run_input_sha256(case, method, payload):
        return evidence
    if any(begin.get(key) != expected[key] for key in (
        "input_sha256", "code_sha256", "argv_sha256", "env_sha256",
    )):
        return evidence
    evidence["request_kind"] = request_kind
    evidence["source_binding"] = {
        "source": "D1 independent JSONL ledger: request and closure only",
        "run_id": run_id,
        "case_id": case["id"],
        "events_sha256": ledger["events_sha256"],
        "coverage": replayed["coverage"],
        **expected,
    }
    kinds = {
        "dynamic_fallback_candidate": {"dynamic_fallback"},
        "explicit_pipeline": {"explicit_sequence"},
        "route_only": {"route_decision"},
    }
    transitions = [
        e
        for e in replayed["events"]
        if e.get("kind") == "transition"
        and e.get("request_kind") in kinds.get(request_kind, ())
        and str(e.get("case_id") or "") in ("", case["id"])
    ]
    values = sorted({str(e.get("transition") or "") for e in transitions})
    evidence["status"] = "verified"
    evidence["reason"] = None
    evidence["transitions"] = values
    if len(values) == 1:
        evidence["observed_transition"] = values[0]
    return evidence


def _line_oracle_ok(output: str, expected: dict) -> bool:
    path = str(expected["path"])
    line = int(expected["line"])
    contains = str(expected["contains"])
    return (
        path in output
        and f":{line}:" in output
        and contains in output
    )


def _word_windows(text: str, size: int) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {
        tuple(words[i : i + size])
        for i in range(len(words) - size + 1)
    }


def _rag_excerpt_evidence(
    case: dict,
    output: str,
    root: Path | None,
) -> tuple[bool, str, list[list[str]]]:
    """Frozen RAG completion evidence: every expected chunk id plus a
    substantive verbatim excerpt of its frozen document."""
    oracle = case.get("oracle") or {}
    chunk_ids = [
        str(chunk) for chunk in oracle.get("expected_chunk_ids") or []
    ]
    if not chunk_ids:
        return False, "case declares no expected chunks", []
    if root is None:
        return (
            False,
            "frozen document unavailable for excerpt verification",
            [],
        )
    manifest_path = root / "docs" / "rag" / "manifest.jsonl"
    manifest: dict[str, str] = {}
    try:
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            manifest[str(entry.get("id"))] = str(entry.get("path"))
    except (OSError, json.JSONDecodeError):
        return False, "frozen rag manifest unreadable", []
    out_windows = _word_windows(output, _RAG_EXCERPT_MIN_WORDS)
    matched: list[list[str]] = []
    for chunk_id in chunk_ids:
        rel = manifest.get(chunk_id)
        if not rel:
            return False, f"chunk {chunk_id} missing from manifest", []
        try:
            body = (root / rel).read_text(encoding="utf-8")
        except OSError:
            return False, f"frozen document {rel} unreadable", []
        found = [
            list(window)
            for window in _word_windows(body, _RAG_EXCERPT_MIN_WORDS)
            & out_windows
        ]
        if not found:
            return (
                False,
                f"output names {chunk_id} without a substantive excerpt",
                [],
            )
        matched.extend(found)
    return True, "chunk ids and frozen-document excerpt verified", matched


def _completion_evidence(
    case: dict,
    *,
    eligible: bool,
    success: bool,
    output: str,
    root: Path | None,
) -> dict[str, Any]:
    """Actual completion evidence — separate from the oracle/contract
    success stored in ``success``."""
    category = _COMPLETION_CATEGORY.get(case["id"], "unclassified")
    if category not in _TASK_COMPLETION_CATEGORIES:
        return {
            "category": category,
            "satisfied": False,
            "detail": _NON_COMPLETION_DETAIL[category],
        }
    if not eligible:
        return {
            "category": category,
            "satisfied": False,
            "detail": "row is not a measured greedy-method run",
        }
    if category == "frozen_search":
        # The frozen search oracle verifies exactly file/line/content.
        return {
            "category": category,
            "satisfied": bool(success),
            "detail": (
                "oracle file/line/content verified"
                if success
                else "oracle file/line/content not satisfied"
            ),
        }
    excerpt_ok, detail, matched = _rag_excerpt_evidence(case, output, root)
    evidence = {
        "category": category,
        "satisfied": bool(success) and excerpt_ok,
        "detail": detail,
    }
    if matched:
        evidence["matched_windows"] = matched[:8]
    return evidence


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
    escalation_evidence = _escalation_evidence(case, raw)
    observed_escalation = escalation_evidence["observed_transition"]
    terminal_ok = all(checks.values()) and raw.get("error") is None
    if expected_escalation:
        checks["escalation"] = observed_escalation == expected_escalation
    if not checks:
        checks["completed"] = exit_code == 0
    success = all(checks.values()) and raw.get("error") is None

    return {
        **raw,
        "success": success,
        "correctness_status": (
            "pass" if success else "unknown" if expected_escalation and terminal_ok else "fail"
        ),
        "checks": checks,
        "route_target": target or None,
        "observed_escalation": observed_escalation,
        "escalation_evidence": escalation_evidence,
    }


def _count_metric(
    value: int | None,
    status: str,
    *,
    source: str | None = None,
    reason: str | None = None,
    scope: str = "invocation",
) -> dict[str, Any]:
    return {
        "value": value,
        "status": status,
        "scope": scope,
        "source": source,
        "reason": reason,
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
        "attempts": _count_metric(
            None,
            "not_applicable",
            reason=reason,
        ),
        "retries": _count_metric(
            None,
            "not_applicable",
            reason=reason,
        ),
        "escalations": [],
        "observation": _coverage_report_unobserved(
            state="not_applicable",
            reason=reason,
        ),
        "completion_eligible": False,
        "completion_evidence": {
            "category": _COMPLETION_CATEGORY.get(case["id"], "unclassified"),
            "satisfied": False,
            "detail": reason,
        },
        "zero_completed": False,
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


def _billing_for_observation(
    evidence_level: str,
    observation: dict[str, Any],
) -> tuple[dict, dict, dict]:
    cursor = _unknown_metric("USD", "Cursor billing unavailable")
    if evidence_level == "contract_stub":
        return (
            _unknown_metric("tokens", "agent contract stub has no token usage"),
            _unknown_metric("USD", "agent contract stub has no billing"),
            cursor,
        )
    if evidence_level == "live_host":
        return (
            _unknown_metric("tokens", "host adapter self-report"),
            _unknown_metric("USD", "host adapter self-report"),
            cursor,
        )
    # The observation ledger is not a billing source: even a complete
    # zero-request run cannot produce measured/authoritative tokens or USD
    # — without an independent usage source the answer stays unknown.
    return (
        _unknown_metric(
            "tokens",
            "no authoritative usage/billing source in observed scope",
        ),
        _unknown_metric(
            "USD",
            "no authoritative usage/billing source in observed scope",
        ),
        cursor,
    )


def _observation_state(observation: dict[str, Any]) -> str:
    return str(
        observation.get("state")
        or (
            "observed"
            if observation.get("run_id")
            else "unobserved"
        )
    )


def _finalize_observation(
    case: dict,
    method: str,
    raw: dict[str, Any],
    *,
    evidence_level: str = "measured",
    reported_attempts: int | None = None,
    reported_retries: int | None = None,
    reported_escalations: list[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    evaluated = _evaluate(case, raw)
    observation = raw.get("observation")
    if not isinstance(observation, dict):
        observation = _coverage_report_unobserved(
            state="contract_stub" if evidence_level == "contract_stub" else "unobserved",
            reason=(
                "contract stub — no execution"
                if evidence_level == "contract_stub"
                else "run was not executed under the observation ledger"
            ),
        )
    llm_tokens, actual_cost, cursor_cost = _billing_for_observation(
        evidence_level, observation
    )

    if evidence_level == "contract_stub":
        attempts = _count_metric(
            None,
            "not_applicable",
            reason="contract stub — no execution",
            scope="pipeline_stages",
        )
        retries = _count_metric(
            None,
            "not_applicable",
            reason="contract stub — no execution",
            scope="pipeline_stages",
        )
        escalations: list[str] = []
    elif reported_attempts is not None:
        attempts = _count_metric(
            reported_attempts,
            "reported",
            source="host adapter self-report",
            scope="pipeline_stages",
        )
        retries = _count_metric(
            reported_retries if reported_retries is not None else 0,
            "reported",
            source="host adapter self-report",
            scope="pipeline_stages",
        )
        escalations = list(reported_escalations or [])
    else:
        # Stage/attempt counts must come from independent ledger events —
        # words like "fallback" in tool output or fixture text are not
        # pipeline evidence. Pipeline internals are not instrumented in
        # the observed scope, so the honest value is unknown.
        attempts = _count_metric(
            None,
            "unknown",
            reason=(
                "pipeline stages not instrumented in observed scope — "
                "not counted from output text"
            ),
            scope="pipeline_stages",
        )
        retries = _count_metric(
            None,
            "unknown",
            reason=(
                "pipeline stages not instrumented in observed scope — "
                "not counted from output text"
            ),
            scope="pipeline_stages",
        )
        escalations = []

    coverage = observation.get("coverage") or {}
    python_scope = observation.get("python_scope") or {}
    success = bool(evaluated["success"])
    # Completion eligibility is a pre-declared per-case contract: only a
    # real task-completion case (frozen search, RAG excerpt) under a
    # measured greedy method qualifies — echo-contract scripts, fallback
    # playbooks, routing/escalation answers, contract stubs, technical
    # failures and wrong results never count as zero_completed.
    category = _COMPLETION_CATEGORY.get(case["id"], "unclassified")
    completion_eligible = bool(
        evidence_level == "measured"
        and method in GREEDY_METHODS
        and category in _TASK_COMPLETION_CATEGORIES
    )
    completion_evidence = _completion_evidence(
        case,
        eligible=completion_eligible,
        success=success,
        output=str(evaluated.get("output") or ""),
        root=root,
    )
    zero_completed = bool(
        completion_eligible
        and completion_evidence["satisfied"]
        and success
        and coverage.get("complete")
        and python_scope.get("model_attempts") == 0
        and python_scope.get("llm_requests_sent") == 0
        and all((observation.get("invocation") or {}).get(key, {}).get("value") == 0
                for key in ("model_attempts", "llm_requests_sent"))
        and (observation.get("invocation") or {}).get("model_attempts", {}).get(
            "status"
        )
        == "observed"
        and (observation.get("invocation") or {}).get("llm_requests_sent", {}).get(
            "status"
        )
        == "observed"
    )

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
        "correctness_status": evaluated["correctness_status"],
        "checks": evaluated["checks"],
        "exit_code": evaluated.get("exit_code"),
        "route_target": evaluated.get("route_target"),
        "observed_escalation": evaluated.get("observed_escalation"),
        "escalation_evidence": evaluated["escalation_evidence"],
        "raw_result": {
            "complete": True,
            **{key: raw.get(key) for key in (
                "exit_code", "output", "error", "duration_ms", "route_target", "run_id",
            )},
        },
        "payload": {
            "output_bytes": len(str(raw.get("output") or "").encode("utf-8")),
            "output_sha256": _sha256_text(str(raw.get("output") or "")),
            "scope": "complete returned product payload, not an uncapped executor result",
        },
        "output_excerpt": str(evaluated.get("output") or "")[:1200],
        "error": evaluated.get("error"),
        "duration_ms": round(float(evaluated["duration_ms"]), 3),
        "run_id": observation.get("run_id"),
        "attempts": attempts,
        "retries": retries,
        "escalations": escalations,
        "observation": observation,
        "completion_eligible": completion_eligible,
        "completion_evidence": completion_evidence,
        "zero_completed": zero_completed,
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


def _run_cli(case: dict, root: Path) -> dict[str, Any]:
    """Unobserved CLI run — live/manual mode only; deterministic runs observe."""
    operation = case["operation"]
    prefix = [sys.executable, "-m", "greedy_token", "--no-log"]
    if operation in ("search", "script", "fallback"):
        argv = [*prefix, "run", case["task"], "--execute"]
    elif operation == "rag":
        argv = [*prefix, "rag", case["query"], "--domain", case["domain"]]
    else:
        argv = [*prefix, "route", case["task"]]
    raw = _run_process(argv, root=root)
    if operation in ("escalation", "route-only"):
        raw["route_target"] = _parse_route_target(raw["output"])
    return _finalize_observation(case, "greedy_cli", raw, root=root)


def _run_mcp(case: dict, root: Path) -> dict[str, Any]:
    """Unobserved MCP run — live/manual mode only; deterministic runs observe."""
    operation = case["operation"]
    if operation == "search":
        tool = "greedy_token_search"
        arguments = {
            "query": case["query"],
            "path": case["scope"],
            "context": "none",
        }
    elif operation in ("script", "fallback"):
        tool = "greedy_token_pipeline"
        arguments = {"task": case["mcp_pipeline"], "execute": True}
    elif operation == "rag":
        tool = "greedy_token_rag"
        arguments = {"query": case["query"], "domain": case["domain"]}
    else:
        tool = "greedy_token_route"
        arguments = {"task": case["task"]}
    raw = _mcp_call(root, tool, arguments)
    if operation in ("escalation", "route-only"):
        raw["route_target"] = _parse_route_target(raw["output"])
    return _finalize_observation(case, "greedy_mcp_stdio", raw, root=root)


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
    return _finalize_observation(
        case,
        "agent_baseline",
        raw,
        evidence_level="contract_stub",
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
        reported_attempts=attempts,
        reported_retries=retries,
        reported_escalations=escalations,
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


_ROUTE_CLASSIFY_CHILD = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from greedy_token.router import route_task\n"
    "cases = json.loads(sys.stdin.read())\n"
    "root = Path(sys.argv[1])\n"
    "for case in cases:\n"
    "    t0 = __import__('time').perf_counter_ns()\n"
    "    d = route_task(case['task'], root)\n"
    "    ms = (__import__('time').perf_counter_ns() - t0) / 1_000_000\n"
    "    print('__ROUTE__' + json.dumps({'case_id': case['id'], "
    "'target': d.target, 'route_id': d.route_id, 'ms': ms}))\n"
)


def _classify_routes_observed(
    cases: list[dict],
    root: Path,
    *,
    ledger_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Route classification inside one observed child — probes stay covered."""
    run_id = _new_run_id()
    env = _observed_child_env(
        root,
        run_id=run_id,
        case_id="__route_classification__",
        ledger_path=ledger_path,
    )
    inputs = [{"id": c["id"], "task": c["task"]} for c in cases]
    raw = _run_observed(
        [
            sys.executable,
            "-S",
            "-c",
            OBSERVED_EXEC_ENTRY,
            _ROUTE_CLASSIFY_CHILD,
            str(root),
        ],
        env=env,
        root=root,
        ledger_path=ledger_path,
        run_id=run_id,
        case_id="__route_classification__",
        method="route_classification",
        input_sha256=_run_input_sha256(
            None, "route_classification", inputs
        ),
        input_text=json.dumps(inputs),
    )
    decisions: dict[str, dict] = {}
    for line in str(raw.get("output") or "").splitlines():
        if not line.startswith("__ROUTE__"):
            continue
        try:
            payload = json.loads(line[len("__ROUTE__"):])
        except json.JSONDecodeError:
            continue
        decisions[str(payload.get("case_id"))] = payload

    rows: list[dict[str, Any]] = []
    for case in cases:
        payload = decisions.get(case["id"]) or {}
        actual = str(payload.get("target") or "")
        expected = case["expected_target"]
        duration = payload.get("ms")
        rows.append(
            {
                "case_id": case["id"],
                "lang": case["lang"],
                "family": case["family"],
                "expected_target": expected,
                "actual_target": actual or None,
                "route_id": payload.get("route_id") or None,
                "ok": bool(actual) and actual == expected,
                "false_cheap": (
                    case["family"] == FALSE_CHEAP_FAMILY
                    and bool(actual)
                    and actual != "cursor"
                ),
                "duration_ms": (
                    round(float(duration), 3)
                    if duration is not None
                    else None
                ),
            }
        )
    return rows, raw["observation"]


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


def _zero_completion_ok(row: dict) -> bool:
    """Independent re-verification of a zero_completed flag — the gate
    recomputes eligibility from components, never trusts the flag."""
    observation = row.get("observation") or {}
    coverage = observation.get("coverage") or {}
    python_scope = observation.get("python_scope") or {}
    invocation = observation.get("invocation") or {}
    evidence = row.get("completion_evidence") or {}
    category = str(evidence.get("category") or "")
    fragment_ok = True
    if category == "rag_excerpt" and evidence.get("satisfied"):
        # Re-verify the stored excerpt claim against the row's own output:
        # at least one matched frozen-document window must be visible in
        # the recorded excerpt, or the satisfied flag was forged.
        out_windows = _word_windows(
            str(row.get("output_excerpt") or ""), _RAG_EXCERPT_MIN_WORDS
        )
        fragment_ok = any(
            tuple(window) in out_windows
            for window in evidence.get("matched_windows") or []
        )
    return bool(
        row.get("success") is True
        and row.get("completion_eligible") is True
        and category
        == _COMPLETION_CATEGORY.get(row.get("case_id"), "unclassified")
        and category in _TASK_COMPLETION_CATEGORIES
        and evidence.get("satisfied") is True
        and fragment_ok
        and row.get("method") in GREEDY_METHODS
        and coverage.get("complete")
        and python_scope.get("model_attempts") == 0
        and python_scope.get("llm_requests_sent") == 0
        and all((invocation.get(key) or {}).get("value") == 0
                for key in ("model_attempts", "llm_requests_sent"))
        and (invocation.get("model_attempts") or {}).get("status") == "observed"
        and (invocation.get("llm_requests_sent") or {}).get("status")
        == "observed"
    )


def _corpus_accounting(corpus: dict, observations: list[dict], repetitions: int) -> dict:
    rows = [r for r in observations if r["applicable"] and r["method"] in GREEDY_METHODS
            and r["evidence_level"] == "measured"]
    complete = [r for r in rows if r["observation"]["coverage"]["complete"]
                and all(r["observation"]["invocation"][key]["status"] == "observed"
                        and isinstance(r["observation"]["invocation"][key]["value"], int)
                        for key in ("model_attempts", "llm_requests_sent"))]
    zero = [r for r in rows if _zero_completion_ok(r) and r.get("zero_completed")]
    payloads = [r["payload"]["output_bytes"] for r in rows
                if (r.get("payload") or {}).get("output_bytes") is not None]
    return {
        "unique_tasks": len(corpus["cases"]),
        "repetitions": repetitions,
        "measured_applicable_greedy_rows": len(rows),
        "not_applicable_rows": sum(not r["applicable"] for r in observations),
        "contract_stub_rows": sum(r["evidence_level"] == "contract_stub" for r in observations),
        "correctness": {
            status: sum(r.get("correctness_status") == status for r in rows)
            for status in ("pass", "fail", "unknown")
        },
        "completion_eligible_rows": sum(r["completion_eligible"] for r in rows),
        "zero_completed_rows": len(zero),
        "zero_completed_unique_tasks": len({r["case_id"] for r in zero}),
        "zero_completed_rate_all_measured_applicable": _rate(len(zero), len(rows)),
        "invocation_counts": {
            "complete_observed_rows": len(complete),
            "incomplete_or_unknown_rows": len(rows) - len(complete),
            "complete_observed_subtotals": {
                key: sum(r["observation"]["invocation"][key]["value"] for r in complete)
                for key in ("model_attempts", "llm_requests_sent")
            },
            "all_applicable_totals": {
                key: {
                    "value": (sum(r["observation"]["invocation"][key]["value"] for r in complete)
                              if len(complete) == len(rows) else None),
                    "status": "observed" if rows and len(complete) == len(rows) else "unknown",
                }
                for key in ("model_attempts", "llm_requests_sent")
            },
        },
        "returned_payload": {
            "complete_payload_rows": len(payloads),
            "unknown_rows": len(rows) - len(payloads),
            "known_bytes_subtotal": sum(payloads),
            "max_known_bytes": max(payloads, default=None),
            "uncapped_executor_payload": "unknown",
        },
        "transitions": {
            "observed": sum(r.get("observed_escalation") is not None for r in rows),
            "unknown_required_rows": sum(
                (r.get("escalation_evidence") or {}).get("status") == "unknown" for r in rows
            ),
            "request_kinds": {
                kind: sum((r.get("escalation_evidence") or {}).get("request_kind") == kind for r in rows)
                for kind in ("dynamic_fallback_candidate", "explicit_pipeline", "route_only", "unknown")
            },
        },
        "product_fallback_and_dedup": {
            "status": "unknown",
            "reason": "public drivers do not bind owned lifecycle/transition counts",
            "dynamic_fallbacks": None,
            "duplicates": None,
        },
        "latency_attribution": {
            "duration_ms_scope": "driver wall clock per invocation, includes attempts and cleanup",
            "total_elapsed": "NOT_MEASURED",
            "model_latency": "NOT_MEASURED",
            "queue_latency": "NOT_MEASURED",
            "host_requests_skipped_turn_savings": "unknown",
        },
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
    observation_meta: dict[str, Any] | None = None,
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
    measured_attempts = sum(
        row["attempts"]["value"]
        for row in observations
        if row["attempts"]["value"] is not None
    )
    measured_retries = sum(
        row["retries"]["value"]
        for row in observations
        if row["retries"]["value"] is not None
    )
    unknown_attempts = sum(
        row["attempts"]["status"] == "unknown" for row in observations
    )
    escalations = sum(len(row["escalations"]) for row in observations)

    observed_rows = [
        row
        for row in observations
        if _observation_state(row.get("observation") or {}) == "observed"
    ]
    python_totals = {
        key: sum(int(row["observation"]["python_scope"][key]) for row in observed_rows)
        for key in (
            "model_attempts",
            "llm_requests_denied",
            "llm_requests_sent",
            "native_launch_denied",
            "native_launch_allowed",
            "native_launch_admitted",
            "trusted_children",
            "trusted_child_admissions",
            "provider_fallbacks",
            "provider_probes",
            "io_http_denied",
            "io_http_allowed",
        )
    }
    coverage_statuses = [
        row["observation"]["coverage"]["status"] for row in observed_rows
    ]
    zero_completed_rows = [
        row for row in observations if row.get("zero_completed")
    ]
    allowed_attempt_status = {"observed", "unknown", "reported", "not_applicable"}

    accounting = _corpus_accounting(corpus, observations, repetitions)
    gates = {
        "corpus_rows_complete": len(observations) == len(corpus["cases"]) * repetitions * len(METHODS)
        and {(r["case_id"], r["method"], r.get("repetition")) for r in observations}
        == {(c["id"], m, repetition) for c in corpus["cases"] for m in METHODS
            for repetition in range(1, repetitions + 1)},
        "mandatory_transition_evidence": all(
            (row.get("escalation_evidence") or {}).get("status") != "unknown"
            for row in observations if row["applicable"] and row["method"] in GREEDY_METHODS
        ),
        "product_completion_evidence_complete": all(
            row["observation"]["coverage"]["complete"]
            for row in observations if row.get("completion_eligible")
        ),
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
            row["attempts"]["status"] in allowed_attempt_status
            and row["retries"]["status"] in allowed_attempt_status
            for row in observations
            if row["applicable"]
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
        "no_real_model_dispatch": python_totals["llm_requests_sent"] == 0,
        "no_zero_without_full_coverage": all(
            row["observation"]["coverage"]["complete"]
            and row["observation"]["python_scope"]["model_attempts"] == 0
            and row["observation"]["python_scope"]["llm_requests_sent"] == 0
            for row in zero_completed_rows
        ),
        "zero_completed_requires_completion": all(
            _zero_completion_ok(row) for row in zero_completed_rows
        ),
        "coverage_gap_means_unknown": all(
            row["observation"]["invocation"]["model_attempts"]["status"]
            == "unknown"
            and row["observation"]["invocation"]["llm_requests_sent"]["status"]
            == "unknown"
            for row in observed_rows
            if row["observation"]["coverage"]["status"] != "complete"
        ),
        "no_method_derived_billing": all(
            row["llm_tokens"]["authoritative"] is False
            or row["llm_tokens"]["source"]
            for row in observations
            if row["applicable"] and row["method"] in GREEDY_METHODS
        ),
        "observed_channel_independent": all(
            row["run_id"]
            and row["observation"]["coverage"]["status"]
            in ("complete", "incomplete")
            and any(
                e.get("kind") == "run_begin"
                for e in row["observation"]["events"]
            )
            and any(
                e.get("kind") == "run_end"
                for e in row["observation"]["events"]
            )
            and any(
                e.get("kind") == "child_start"
                for e in row["observation"]["events"]
            )
            and all(
                e.get("run_id") == row["run_id"]
                for e in row["observation"]["events"]
                if e.get("kind") not in ("bootstrap", "ledger_corrupt")
            )
            for row in observed_rows
        ),
    }
    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "grading": {
            "grader_sha256": _sha256(Path(__file__)),
            "oracle_corpus_sha256": lock.get("sha256"),
            "oracle_case_sha256": {
                case["id"]: _sha256_text(json.dumps(case.get("oracle") or {}, sort_keys=True, ensure_ascii=False))
                for case in corpus["cases"]
            },
            "input_case_sha256": {
                case["id"]: _sha256_text(json.dumps(case, sort_keys=True, ensure_ascii=False))
                for case in corpus["cases"]
            },
        },
        "execution_bindings": {
            "code_sha256": _code_sha256(),
            "source_sha256": {rel: _sha256(REPO_ROOT / rel) for rel in _CODE_WATCH},
        },
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
            "corpus_accounting": accounting,
            "attempts": {
                "total_attempts": None if unknown_attempts else measured_attempts,
                "known_attempts_subtotal": measured_attempts,
                "retries": None if unknown_attempts else measured_retries,
                "known_retries_subtotal": measured_retries,
                "escalations": None if accounting["transitions"]["unknown_required_rows"] else escalations,
                "known_escalations_subtotal": escalations,
                "unknown_attempt_runs": unknown_attempts,
                "scope": (
                    "pipeline_stages: stage events are not instrumented in "
                    "the observed scope — values are unknown, never "
                    "derived from tool output text"
                ),
                "latency_scope": "wall clock includes all attempts in each run",
            },
            "observation": {
                "channel": observation_meta or {},
                "runs": {
                    "observed": len(observed_rows),
                    "coverage_complete": coverage_statuses.count("complete"),
                    "coverage_incomplete": coverage_statuses.count(
                        "incomplete"
                    ),
                    "unobserved": coverage_statuses.count("unobserved"),
                },
                "python_scope_totals": python_totals,
                "zero_completed_runs": len(zero_completed_rows),
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


def _regrade_scorecard(source_path: Path, corpus: dict, lock: dict) -> dict:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    original_benchmark = source["benchmark"]
    if (original_benchmark["corpus_lock"]["sha256"] != lock["sha256"]
            or original_benchmark["id"] != corpus["corpus"]["id"]):
        raise ValueError("replay corpus/input binding mismatch")
    cases = {case["id"]: case for case in corpus["cases"]}
    observations = []
    ledger_replays = 0
    coverage_changes = 0
    with tempfile.TemporaryDirectory(prefix="greedy-token-regrade-") as tmp:
        root = Path(tmp)
        _write_fixture(corpus, root)
        for original in source["observations"]:
            if not original["applicable"]:
                observations.append(dict(original))
                continue
            case = cases[original["case_id"]]
            raw = dict(original.get("raw_result") or {
                "exit_code": original["exit_code"],
                "output": original.get("output_excerpt") or "",
                "error": original.get("error"),
                "duration_ms": original["duration_ms"],
                "route_target": original.get("route_target"),
                "run_id": original.get("run_id"),
            })
            complete_payload = raw.get("complete") is True
            original_observation = original.get("observation") or {}
            replayed = _replay_observation(original_observation)
            if replayed is not None:
                ledger_replays += 1
                ledger = original_observation["ledger"]
                if ledger.get("events_sha256") != _events_sha256(ledger.get("events") or []):
                    replayed["coverage"] = {
                        "status": "incomplete", "complete": False,
                        "reasons": [*replayed["coverage"]["reasons"], "stored_ledger_digest_mismatch"],
                    }
                    replayed["invocation"] = {
                        "model_attempts": _unknown_count(), "llm_requests_sent": _unknown_count(),
                    }
                coverage_changes += replayed["coverage"] != original_observation.get("coverage")
                replayed["ledger"] = ledger
                raw["observation"] = replayed
            else:
                raw["observation"] = _coverage_report_unobserved(
                    state="contract_stub" if original["evidence_level"] == "contract_stub" else "unobserved",
                    reason="historical row has no replayable raw ledger",
                )
            row = _finalize_observation(
                case, original["method"], raw, root=root,
                evidence_level=original["evidence_level"],
            )
            row["repetition"] = original["repetition"]
            row["raw_result"]["complete"] = complete_payload
            if not complete_payload:
                row["payload"] = {
                    "output_bytes": None, "output_sha256": None,
                    "scope": "historical excerpt only; complete payload unavailable",
                }
                if (row["correctness_status"] == "fail" and row["error"] is None
                        and row["checks"].get("exit_code", True)):
                    row["correctness_status"] = "unknown"
            row["regrade"] = {
                "original_success": original["success"],
                "original_zero_completed": original.get("zero_completed"),
                "raw_scope": "complete_payload" if complete_payload else "historical_excerpt",
                "ledger_replayed": replayed is not None,
                "execution_repeated": False,
            }
            observations.append(row)
    channel = dict((source["summary"].get("observation") or {}).get("channel") or {})
    route_observation = channel.get("route_classification_observation")
    if isinstance(route_observation, dict):
        replayed_routes = _replay_observation(route_observation)
        if replayed_routes is not None:
            ledger_replays += 1
            coverage_changes += replayed_routes["coverage"] != route_observation["coverage"]
    result = _build_scorecard(
        corpus=corpus, lock=lock, mode=original_benchmark["mode"],
        repetitions=original_benchmark["repetitions"],
        route_rows=source["route_classification"], observations=observations,
        live_probes=source.get("live_probes") or {},
        allow_metered_api=original_benchmark["billing_policy"]["metered_api_allowed"],
        observation_meta=channel,
    )
    result["benchmark"] = original_benchmark
    result["execution_bindings"] = source.get("execution_bindings") or {
        "code_sha256": channel.get("code_sha256"),
        "source_sha256": None,
        "reason": "historical snapshot did not record per-module execution hashes",
    }
    result["regrade"] = {
        "source_artifact": str(source_path.resolve()),
        "source_artifact_sha256": _sha256(source_path),
        "original_generated_at": source["generated_at"],
        "grader_sha256": result["grading"]["grader_sha256"],
        "execution_repeated": False,
        "route_classification": "historical predictions, not rerun with the current router",
        "raw_ledger_replays": ledger_replays,
        "coverage_verdict_changes": coverage_changes,
        "external_ledger_files": "not reread; embedded complete validator inputs replayed",
        "changed_outcome_rows": sum(
            r.get("regrade", {}).get("original_success") != r["success"]
            for r in observations if r["applicable"]
        ),
    }
    return result


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
    observation = scorecard["summary"].get("observation") or {}
    runs = observation.get("runs") or {}
    totals = observation.get("python_scope_totals") or {}
    if runs:
        print(
            "observed runs: "
            f"{runs.get('observed', 0)} "
            f"(complete={runs.get('coverage_complete', 0)}, "
            f"incomplete={runs.get('coverage_incomplete', 0)}, "
            f"unobserved={runs.get('unobserved', 0)}); "
            f"python-scope model_attempts={totals.get('model_attempts', 0)}, "
            f"llm_requests_sent={totals.get('llm_requests_sent', 0)}, "
            f"native_denied={totals.get('native_launch_denied', 0)}, "
            f"zero_completed={observation.get('zero_completed_runs', 0)}"
        )
    print(f"gates: {'PASS' if scorecard['gates']['all_passed'] else 'FAIL'}")
    print("EVIDENCE_REPORT " + json.dumps({
        "gates": scorecard["gates"],
        "corpus_accounting": scorecard["summary"]["corpus_accounting"],
        "attempts": scorecard["summary"]["attempts"],
        "layers": scorecard["summary"]["task_success"]["by_layer"],
        "grading": scorecard["grading"],
        "execution_bindings": scorecard["execution_bindings"],
        "regrade": scorecard.get("regrade"),
    }, ensure_ascii=False, sort_keys=True))


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
    parser.add_argument("--replay", type=Path, help="Regrade an existing scorecard without task execution")
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

    output = args.output.resolve()
    if output.exists():
        raise SystemExit("output already exists; preserve artifacts and choose a new --output")
    if args.replay:
        scorecard = _regrade_scorecard(args.replay.resolve(), corpus, lock)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(scorecard, indent=2, ensure_ascii=False) + "\n")
        _print_summary(scorecard)
        print(f"scorecard: {output}")
        return 0 if scorecard["gates"]["all_passed"] else 1

    live_probes: dict[str, Any] = {}
    observation_meta: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="greedy-token-evidence-") as tmp:
        root = Path(tmp)
        # route_task() runs in-process — resolve settings against the fixture
        # workspace like the subprocess env does, not the caller's checkout.
        old_root_env = os.environ.get("GREEDY_TOKEN_ROOT")
        os.environ["GREEDY_TOKEN_ROOT"] = str(root)
        try:
            if args.mode == "deterministic":
                with _ollama_stub() as stub_url:
                    old_url = os.environ.get("OLLAMA_URL")
                    old_bench = os.environ.get("BENCH_MODEL")
                    old_model = os.environ.pop("OLLAMA_MODEL", None)
                    os.environ["OLLAMA_URL"] = stub_url
                    os.environ["BENCH_MODEL"] = "evidence-stub"
                    try:
                        _write_fixture(corpus, root)
                        ledger_path = root / "_observed" / "ledger.jsonl"
                        bootstrap = _observe_bootstrap(ledger_path)
                        route_rows, route_observation = (
                            _classify_routes_observed(
                                cases, root, ledger_path=ledger_path
                            )
                        )
                        observations = _run_all(
                            cases,
                            root,
                            repetitions=args.repetitions,
                            host_command="",
                            mode=args.mode,
                            ledger_path=ledger_path,
                        )
                        observation_meta = {
                            "type": "independent_jsonl",
                            "scope": OBSERVATION_SCOPE,
                            "code_sha256": bootstrap["code_sha256"],
                            "ledger_sha256": _sha256(ledger_path),
                            "ephemeral": True,
                            "route_classification_observation": (
                                route_observation
                            ),
                            "note": (
                                "ledger lives inside the temporary fixture; "
                                "per-run events are embedded in each "
                                "observation row"
                            ),
                        }
                    finally:
                        if old_url is None:
                            os.environ.pop("OLLAMA_URL", None)
                        else:
                            os.environ["OLLAMA_URL"] = old_url
                        if old_bench is None:
                            os.environ.pop("BENCH_MODEL", None)
                        else:
                            os.environ["BENCH_MODEL"] = old_bench
                        if old_model is not None:
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
                    _bench_model("qwen2.5-coder:7b-instruct-q4_K_M"),
                )
                live_probes["mcp_stdio"] = _live_mcp_probe(root)
                live_probes["agent_host"] = {
                    "status": "measured" if args.host_command else "skipped",
                    "billing": args.host_billing,
                    "cost_rule": (
                        "authoritative adapter value only; otherwise unknown"
                    ),
                }
        finally:
            if old_root_env is None:
                os.environ.pop("GREEDY_TOKEN_ROOT", None)
            else:
                os.environ["GREEDY_TOKEN_ROOT"] = old_root_env

    scorecard = _build_scorecard(
        corpus=corpus,
        lock=lock,
        mode=args.mode,
        repetitions=args.repetitions,
        route_rows=route_rows,
        observations=observations,
        live_probes=live_probes,
        allow_metered_api=args.allow_metered_api,
        observation_meta=observation_meta,
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
    ledger_path: Path | None = None,
) -> list[dict]:
    observed = ledger_path is not None
    observations: list[dict] = []
    for repetition in range(1, repetitions + 1):
        for case in cases:
            if observed:
                rows = [
                    _not_applicable(
                        case,
                        METHODS[0],
                        "native baseline is outside the observed "
                        "python_provider scope — not executed under coverage",
                    ),
                    _run_observed_cli(case, root=root, ledger_path=ledger_path),
                    _run_observed_mcp_case(
                        case, root=root, ledger_path=ledger_path
                    ),
                ]
            else:
                rows = [
                    _not_applicable(
                        case,
                        METHODS[0],
                        "direct baseline is intentionally limited to rg and "
                        "scripts; unobserved native launch stays out of scope",
                    ),
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
