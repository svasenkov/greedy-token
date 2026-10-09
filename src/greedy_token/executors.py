from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from greedy_token.paths import find_workspace_root, workspace_trusted_script_paths
from greedy_token.rag_search import format_hits, search_rag
from greedy_token.result_contract import RESULT_NOT_EVALUATED, evaluate_script_result
from greedy_token.result_gate import GateDecision, evaluate_result_gate
from greedy_token.router import RouteDecision, route_task
from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    command_to_argv,
    format_invocation,
    trusted_script_argv,
    trusted_tool_invocation,
)
from greedy_token.tool_output import cap_tool_output, filter_tool_output
from greedy_token.tool_paths import RG_TIMEOUT, SCRIPT_TIMEOUT
from greedy_token.trust import (
    TrustError,
    VerifiedScript,
    _open_script,
    _sha256_fd,
    bind_verified_argv,
    trusted_manifest_paths,
    verify_script,
)
from greedy_token.wrappers import wrapper_for_command


class ProductLifecycleError(RuntimeError):
    pass


class ProductRetryError(RuntimeError):
    pass


@dataclass
class _OwnedCall:
    op: str
    attempts: int = 0
    retries: int = 0
    fallbacks: int = 0
    parent_attempt_id: str = ""


def _product_param(value):
    if isinstance(value, Path):
        return str(value.resolve())
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Unsupported product input type: {type(value).__name__}")


class ProductInvocation:
    def __init__(
        self,
        root: Path,
        *,
        deadline: float | None = None,
        max_requests: int = 64,
        max_attempts: int = 16,
        max_retries: int = 0,
        max_fallbacks: int = 2,
    ):
        for value, minimum in (
            (max_requests, 1), (max_attempts, 1), (max_retries, 0), (max_fallbacks, 0),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError("Product lifecycle bounds must be finite nonnegative integers")
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("Product deadline must be finite")
        self.root = root.resolve()
        self.deadline = deadline
        self.max_requests = max_requests
        self.max_attempts = max_attempts
        self.max_retries = max_retries
        self.max_fallbacks = max_fallbacks
        self._id = uuid.uuid4().hex
        self._condition = threading.Condition()
        self._local = threading.local()
        self._results: dict[tuple, tuple[int, Future]] = {}
        self._active: dict[int, int] = {}
        self._opened = False
        self._closing = False
        self._closed = False
        self._cancelled = False
        self._counts = dict(requests=0, duplicates=0, dispatch_attempts=0, retries=0, fallbacks=0)

    def __enter__(self):
        with self._condition:
            if self._opened or self._closed:
                raise ProductLifecycleError("Product invocation is already open or closed")
            self._opened = True
        return self

    def __exit__(self, *_exc):
        self.close()

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def retained_results(self) -> int:
        with self._condition:
            return len(self._results)

    @property
    def counts(self) -> dict:
        with self._condition:
            return {
                "source": "owned_product_boundary",
                "scope": "declared_product_invocation",
                "measurement_status": "observed",
                **self._counts,
                "active": sum(self._active.values()),
            }

    def _observe(self, kind: str, **fields) -> None:
        from greedy_token.cheap_llm import _observe_emit

        _observe_emit(
            kind, product_invocation_id=self._id,
            source="owned_product_boundary", scope="declared_product_invocation", **fields,
        )

    def _check(self) -> float | None:
        if self._cancelled:
            raise ProductLifecycleError("Product invocation cancelled")
        remaining = None if self.deadline is None else self.deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise ProductLifecycleError("Product invocation deadline exceeded")
        return remaining

    def timeout(self, default: float) -> float:
        with self._condition:
            remaining = self._check()
            if math.isnan(default) or default <= 0:
                raise ProductLifecycleError("Product invocation timeout budget exhausted")
            return default if remaining is None else min(default, remaining)

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            if threading.get_ident() in self._active:
                raise ProductLifecycleError("Cannot close product invocation from owned work")
            self._closing = True
            self._condition.wait_for(lambda: not self._active)
            if self._closed:
                return
            self._results.clear()
            self._closed = True
            self._opened = False
            self._observe("product_close", active=0, **self._counts)
            self._condition.notify_all()

    def run[T](
        self,
        producer: Callable[[], T],
        *,
        root: Path,
        op: str,
        params,
        request_id: str | None = None,
        prompt_id: str | None = None,
        input_version: str | int | None = None,
    ) -> T:
        if root.resolve() != self.root:
            raise ProductLifecycleError("Product invocation root confinement mismatch")
        identity = tuple(
            (name, value) for name, value in (("request_id", request_id), ("prompt_id", prompt_id))
            if isinstance(value, str) and value
        )
        key = None
        if identity and type(input_version) in (str, int) and input_version != "":
            encoded = json.dumps(
                params, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                default=_product_param, allow_nan=False,
            )
            key = (
                identity, type(input_version).__name__, input_version, op,
                hashlib.sha256(encoded.encode()).hexdigest(),
            )
        thread = threading.get_ident()
        with self._condition:
            if getattr(self._local, "call", None) is not None:
                raise ProductLifecycleError("Product recursive invocation denied")
            if not self._opened or self._closing or self._closed:
                raise ProductLifecycleError("Product invocation is closed or not open")
            self._check()
            prior = self._results.get(key) if key is not None else None
            if prior is not None:
                owner, future = prior
                if owner == thread and not future.done():
                    raise ProductLifecycleError("Recursive duplicate product request denied")
                self._counts["duplicates"] += 1
                owns_result = False
            else:
                if self._counts["requests"] >= self.max_requests:
                    raise ProductLifecycleError("Product request limit exceeded")
                future = Future()
                if key is not None:
                    self._results[key] = (thread, future)
                self._counts["requests"] += 1
                owns_result = True
            self._active[thread] = self._active.get(thread, 0) + 1
        previous = getattr(self._local, "call", None)
        try:
            if not owns_result:
                timeout = None if self.deadline is None else self.timeout(float("inf"))
                try:
                    return deepcopy(future.result(timeout=timeout))
                except TimeoutError as exc:
                    if not future.done():
                        raise ProductLifecycleError("Product invocation deadline exceeded") from exc
                    raise
            self._local.call = _OwnedCall(op=op)
            try:
                result = producer()
                future.set_result(deepcopy(result))
            except BaseException as exc:
                future.set_exception(exc)
                raise
            return result
        finally:
            self._local.call = previous
            with self._condition:
                self._active[thread] -= 1
                if not self._active[thread]:
                    del self._active[thread]
                self._condition.notify_all()

    def dispatch[T](self, producer: Callable[[], T], *, cause: str) -> T:
        call = getattr(self._local, "call", None)
        if call is None:
            raise ProductLifecycleError("Product dispatch requires active owned work")
        fallback = cause == "fallback"
        retry_error = None
        while True:
            with self._condition:
                self._check()
                if call.attempts >= self.max_attempts:
                    raise ProductLifecycleError("Product attempt limit exceeded")
                if retry_error is not None and call.retries >= self.max_retries:
                    raise ProductLifecycleError("Product retry limit exceeded") from retry_error
                if fallback and call.fallbacks >= self.max_fallbacks:
                    raise ProductLifecycleError("Product fallback limit exceeded")
                call.attempts += 1
                self._counts["dispatch_attempts"] += 1
                if retry_error is not None:
                    call.retries += 1
                    self._counts["retries"] += 1
                    cause = "retry"
                if fallback:
                    call.fallbacks += 1
                    self._counts["fallbacks"] += 1
                attempt_id = "product-" + uuid.uuid4().hex
                self._observe(
                    "product_attempt", op=call.op, attempt_id=attempt_id, cause=cause,
                    parent_attempt_id=call.parent_attempt_id,
                )
                call.parent_attempt_id = attempt_id
            try:
                result = producer()
            except ProductRetryError as exc:
                retry_error = exc
                self._observe("product_attempt_end", attempt_id=attempt_id, outcome="retry_requested")
            except BaseException:
                self._observe("product_attempt_end", attempt_id=attempt_id, outcome="error")
                raise
            else:
                self._observe("product_attempt_end", attempt_id=attempt_id, outcome="terminal")
                return result


@dataclass
class RunPlan:
    decision: RouteDecision
    command: str | None
    dry_run_output: str
    executable: bool
    argv: tuple[str, ...] | None = None
    cwd: Path | None = None
    authorization: str = ""
    script_path: str = ""
    script_type: str = ""
    refusal_reason: str = ""
    # Refusal class (not_approved / stale_bytes / missing_file / symlink /
    # untrusted_type / …) when the refusal came from a trust decision.
    refusal_code: str = ""


@dataclass(frozen=True)
class PlanRunResult:
    """execute_plan result plus whether the executor was actually started.

    Unpacks as the historical ``(exit_code, output)`` tuple, so callers that only
    need those two keep working; ``started`` exists because a refusal, a missing
    executable, and a real failing command all share exit codes.  ``result_status``
    is the SCRIPT-CANON verdict on the output contract (produced / invalid /
    not_evaluated) — exit_code alone is never a validated result.
    """

    exit_code: int
    output: str
    started: bool = False
    result_status: str = RESULT_NOT_EVALUATED

    def __iter__(self):
        return iter((self.exit_code, self.output))


@dataclass
class TaskRunResult:
    decision: RouteDecision
    output: str
    used_rag_fallback: bool = False
    exit_code: int = 0
    # Observed fact: the executor process really started. False also covers
    # "never observed" — it is never inferred from a request to execute.
    started: bool = False
    # SCRIPT-CANON result-contract verdict for script-tier output.
    result_status: str = RESULT_NOT_EVALUATED


def _prompt_derived_args(task: str, decision: RouteDecision, root: Path) -> tuple[str, ...]:
    """Prompt-derived argv tail for ``params: [args]`` routes.

    Same first-match ``args_from_prompt`` rule order as the hook intercept
    path (``hook_policy.derive_prompt_args``): the first spec entry whose
    regex hits the task renders its ``args`` template.  Fail-open — a route
    without the args contract, a missed regex or an unparseable template
    yields no extra tokens.  Whatever is returned still passes through
    ``trusted_script_argv`` confinement exactly like invoke ``--args``.
    """
    text = task.strip()
    if not text:
        return ()
    try:
        from greedy_token.hook_policy import derive_prompt_args
        from greedy_token.paths import load_routes_config

        routes = load_routes_config(root).get("routes", [])
    except (Exception, SystemExit):
        return ()
    route = next(
        (
            r
            for r in routes
            if isinstance(r, dict) and r.get("id") == decision.route_id
        ),
        None,
    )
    if not isinstance(route, dict):
        return ()
    declared = route.get("params") or []
    if not isinstance(declared, (list, tuple)):
        declared = [declared]
    spec = route.get("args_from_prompt")
    if "args" not in declared or not isinstance(spec, list):
        return ()
    derived = derive_prompt_args(text, spec)
    if not derived.strip():
        return ()
    try:
        return tuple(shlex.split(derived))
    except ValueError:
        return ()


def plan_run(decision: RouteDecision, task: str, root: Path | None = None) -> RunPlan:
    root = root or find_workspace_root()
    target = decision.target

    if target == "tool" and decision.command:
        try:
            if decision.command_argv is None or decision.command_cwd is None:
                raise UnsafeCommandError(
                    "tool command was not produced by the internal argv builder"
                )
            invocation = trusted_tool_invocation(
                decision.command_argv,
                cwd=decision.command_cwd,
                root=root,
                tool=decision.tool,
            )
        except (UnsafeCommandError, OSError) as exc:
            return RunPlan(
                decision=decision,
                command=decision.command,
                dry_run_output=decision.command,
                executable=False,
                refusal_reason=str(exc),
                refusal_code=getattr(exc, "code", ""),
            )
        return RunPlan(
            decision=decision,
            command=decision.command,
            dry_run_output=decision.command,
            executable=decision.read_only,
            argv=invocation.argv,
            cwd=invocation.cwd,
            authorization=invocation.authorization,
        )

    if target in ("python", "ollama") and decision.command:
        wrapper = wrapper_for_command(decision.command)
        registered = (
            (wrapper.path,)
            if wrapper is not None and wrapper.read_only
            else ()
        )
        try:
            approved = trusted_manifest_paths(root) if decision.read_only else ()
            deprecated = (
                workspace_trusted_script_paths(root) if decision.read_only else ()
            )
            argv = decision.command_argv
            cwd = decision.command_cwd
            if argv is None or cwd is None:
                # Legacy route compatibility only; execution below remains argv/cwd.
                parsed_cwd, parsed_argv = command_to_argv(
                    decision.command,
                    # equivalent: workspace_root is always set here, and
                    # command_to_argv normalises a missing cwd via
                    # (cwd or root).resolve() — a dropped/None default_cwd
                    # resolves to the same root.
                    default_cwd=root,
                    workspace_root=root,
                )
                argv = tuple(parsed_argv)
                cwd = parsed_cwd or root
            derived = _prompt_derived_args(task, decision, root)
            if derived:
                # Prompt-derived tail goes after the fixed command argv —
                # same contract as invoke --args; confinement runs below.
                argv = (*argv, *derived)
            invocation = trusted_script_argv(
                argv,
                cwd=cwd,
                root=root,
                registered_script_paths=registered,
                manifest_script_paths=approved,
                trusted_script_paths=deprecated,
            )
        except (UnsafeCommandError, TrustError, OSError) as exc:
            dry_run = (
                format_invocation(decision.command_argv, decision.command_cwd)
                if decision.command_argv is not None and decision.command_cwd is not None
                else decision.command
            )
            return RunPlan(
                decision=decision,
                command=decision.command,
                dry_run_output=dry_run,
                executable=False,
                refusal_reason=str(exc),
                refusal_code=getattr(exc, "code", ""),
            )
        dry_run = format_invocation(invocation.argv, invocation.cwd)
        if target == "ollama":
            dry_run += "  # pass args as needed"
        return RunPlan(
            decision=decision,
            command=decision.command,
            dry_run_output=dry_run,
            executable=True,
            argv=invocation.argv,
            cwd=invocation.cwd,
            authorization=invocation.authorization,
            script_path=invocation.script_path,
            script_type=invocation.script_type,
        )

    if target == "rag":
        hits = search_rag(task, root, domains=decision.domains or None)
        return RunPlan(
            decision=decision,
            command=None,
            dry_run_output=format_hits(task, hits),
            executable=False,
        )

    if target == "cursor":
        return RunPlan(
            decision=decision,
            command=None,
            dry_run_output=(
                "Open new Cursor chat.\n"
                f"Task: {task}\n"
                "Before paste: greedy-token audit-context && greedy-token rag \"<topic>\""
            ),
            executable=False,
        )

    return RunPlan(
        decision=decision,
        command=None,
        dry_run_output="No executor.",
        executable=False,
    )


def _observed_armed() -> bool:
    from greedy_token.cheap_llm import observation_armed

    return observation_armed()


def _rg_argv_tail(argv: tuple[str, ...]) -> tuple[str, list[str], int]:
    """Query, scope operands and --max-count from a routed rg argv."""
    args = list(argv)
    if "--" not in args:
        return "", [], 50
    idx = args.index("--")
    tail = args[idx + 1 :]
    limit = 50
    head = args[:idx]
    if "--max-count" in head:
        try:
            limit = int(head[head.index("--max-count") + 1])
        except (IndexError, ValueError):
            limit = 50
    query = tail[0] if tail else ""
    return query, tail[1:], limit


def _observed_tool_plan(plan: RunPlan) -> PlanRunResult:
    """Answer an rg tool plan with the in-process python search backend.

    Under observation a native rg launch would be denied anyway — the
    capability contract is to select the python backend *before* any
    launch attempt and to keep rg's raw ``path:line:content`` contract so
    downstream filtering and the frozen oracle see real evidence.
    """
    from greedy_token.cheap_llm import observe_search_backend
    from greedy_token.code_search import (
        _python_search_file,
        _python_search_tree,
        search_scope_paths,
    )

    query, operands, limit = _rg_argv_tail(plan.argv or ())
    cwd = plan.cwd
    scope_dirs = [cwd / operand for operand in operands]
    if not scope_dirs:
        scope_dirs = [cwd / p for p in search_scope_paths(cwd)]
    lines: list[str] = []
    for base in scope_dirs:
        remaining = limit - len(lines)
        if remaining <= 0:
            break
        if base.is_file():
            try:
                display = base.relative_to(cwd).as_posix()
            except ValueError:
                display = str(base)
            lines.extend(
                _python_search_file(base, query, limit=remaining, display_path=display)
            )
        elif base.is_dir():
            lines.extend(
                _python_search_tree(cwd, query, scope_dirs=[base], limit=remaining)
            )
    observe_search_backend(
        engine="python",
        scope=", ".join(operands) or "workspace",
        hit_count=len(lines),
        native="skipped",
    )
    if not lines:
        return PlanRunResult(1, "", started=True)
    return PlanRunResult(0, "\n".join(lines) + "\n", started=True)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _spawn_observed_python_child(
    *,
    argv: tuple[str, ...],
    script_path: str,
    cwd: Path,
    authority: str,
    fd: int,
    source_sha256: str,
    timeout: float,
    invocation: ProductInvocation | None = None,
) -> subprocess.CompletedProcess:
    """Spawn the canonical trusted runner under the observed-child admission.

    The parent registers its own independently measured expectations
    (source hash from the open FD, runner code hash, argv/env/pass_fds/cwd,
    file identity); the coverage validator only counts the child when the
    runner's bind event reports the same source/FD/env identity — a child
    self-report alone is never evidence.
    """
    from greedy_token.cheap_llm import (
        OBSERVE_ADMISSION_ENV,
        admit_trusted_child,
        new_child_admission_id,
    )
    from greedy_token.trust import _trusted_runner_path

    runner = str(_trusted_runner_path())
    fd_stat = os.fstat(fd)
    child_argv = [sys.executable, runner, str(fd), script_path, *argv]
    env = dict(os.environ)
    admission_id = new_child_admission_id()
    env[OBSERVE_ADMISSION_ENV] = admission_id
    if invocation is not None:
        timeout = invocation.timeout(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ProductLifecycleError(
            "Executor timeout budget must be positive and finite"
        )
    admit_trusted_child(
        admission_id=admission_id,
        argv=child_argv,
        env=env,
        pass_fds=(fd,),
        cwd=str(cwd),
        runner_sha256=_file_sha256(runner),
        source_sha256=source_sha256,
        source_bytes=fd_stat.st_size,
        script_path=script_path,
        authority=authority,
        fd_device=fd_stat.st_dev,
        fd_inode=fd_stat.st_ino,
    )
    # The child reads the script bytes from this descriptor: rewind the
    # shared open-file description so it sees the approved bytes from 0.
    os.lseek(fd, 0, os.SEEK_SET)
    return subprocess.run(
        child_argv,
        shell=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        pass_fds=(fd,),
        timeout=timeout,
    )


def execute_plan(
    plan: RunPlan, *, timeout: float | None = None, invocation: ProductInvocation | None = None,
) -> PlanRunResult:
    if not plan.command and not plan.argv:
        return PlanRunResult(0, plan.dry_run_output)
    if not plan.executable:
        reason = (
            f" Trust boundary: {plan.refusal_reason}."
            if plan.refusal_reason
            else ""
        )
        code = f" [{plan.refusal_code}]" if plan.refusal_code else ""
        return PlanRunResult(
            1,
            (
                f"Refusing --execute: route is not authorised for execution{code}.{reason}\n"
                f"Dry-run:\n{plan.dry_run_output}\n\n"
                "read_only is metadata, not execution authority."
            ),
        )
    if plan.argv is None or plan.cwd is None or not plan.authorization:
        return PlanRunResult(
            1,
            (
                "Refusing --execute: structured trusted argv is missing.\n"
                f"Dry-run:\n{plan.dry_run_output}"
            ),
        )
    limit = RG_TIMEOUT if plan.decision.target == "tool" else SCRIPT_TIMEOUT
    timeout = limit if timeout is None else min(timeout, limit)
    verified: VerifiedScript | None = None
    wrapper_fd = -1
    try:
        argv = list(plan.argv)
        # equivalent: None and an empty tuple are both falsy here and are replaced before any manifest descriptor is forwarded.
        pass_fds: tuple[int, ...] = ()  # pragma: no mutate
        proc = None
        armed = _observed_armed()
        if (
            armed
            and plan.decision.target == "tool"
            and plan.authorization == "internal-tool:rg"
        ):
            return _observed_tool_plan(plan)
        if plan.authorization.startswith("manifest:"):
            if not plan.script_path or not plan.script_type:
                raise TrustError("manifest-authorised plan is missing script metadata")
            revalidated = trusted_script_argv(
                plan.argv,
                cwd=plan.cwd,
                root=plan.cwd,
                manifest_script_paths=(plan.script_path,),
            )
            if (
                revalidated.authorization != plan.authorization
                or revalidated.script_path != plan.script_path
                or revalidated.script_type != plan.script_type
            ):
                raise TrustError("manifest-authorised argv changed after planning")
            verified = verify_script(plan.cwd, plan.script_path)
            argv, pass_fds = bind_verified_argv(verified, plan.argv)
            if armed and plan.script_type == "python":
                # Bound argv: [python, runner, fd, script, args...].  The
                # observed child needs the verified FD; source identity is
                # hashed off the descriptor the runner will consume, not
                # the manifest entry.
                if len(argv) < 4 or not pass_fds:
                    raise TrustError(
                        "observed Python child requires FD-bound execution"
                    )
                proc = _spawn_observed_python_child(
                    argv=tuple(argv[4:]),
                    script_path=argv[3],
                    cwd=plan.cwd,
                    authority=plan.authorization,
                    fd=verified.fd,
                    source_sha256=_sha256_fd(verified.fd),
                    timeout=timeout,
                    invocation=invocation,
                )
        elif (
            armed
            and plan.authorization.startswith("wrapper:")
            and plan.script_type == "python"
            and plan.script_path
        ):
            if len(plan.argv) < 2:
                raise TrustError("wrapper Python invocation has no script argv")
            # Wrapper authority is registration, not a manifest grant: open
            # the registered source nofollow and let the child bind prove
            # it consumed exactly those bytes.
            wrapper_fd, _stat = _open_script(plan.cwd, plan.script_path)
            proc = _spawn_observed_python_child(
                argv=tuple(plan.argv[2:]),
                script_path=plan.argv[1],
                cwd=plan.cwd,
                authority=plan.authorization,
                fd=wrapper_fd,
                source_sha256=_sha256_fd(wrapper_fd),
                timeout=timeout,
                invocation=invocation,
            )
        if proc is None:
            run_kwargs = {
                "shell": False,
                "capture_output": True,
                "text": True,
                "cwd": plan.cwd,
            }
            if pass_fds:
                run_kwargs["pass_fds"] = pass_fds
            if invocation is not None:
                timeout = invocation.timeout(timeout)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ProductLifecycleError("Executor timeout budget must be positive and finite")
            run_kwargs["timeout"] = timeout
            proc = subprocess.run(
                argv,
                **run_kwargs,
            )
    except TrustError as exc:
        # equivalent: `code` is rendered only when truthy — the "" and None
        # defaults both produce an empty tag for codeless errors.
        code = getattr(exc, "code", "")  # pragma: no mutate
        tag = f" [{code}]" if code else ""
        return PlanRunResult(
            1, f"Refusing --execute: trust verification failed{tag}: {exc}"
        )
    except FileNotFoundError as exc:
        return PlanRunResult(127, f"Executable not found: {exc}")
    except OSError as exc:
        return PlanRunResult(126, f"Cannot execute command: {exc}")
    except subprocess.TimeoutExpired:
        # It did start — it was killed for running too long.
        return PlanRunResult(
            124, f"Command timed out after {timeout}s: {plan.command}", started=True
        )
    finally:
        if verified is not None:
            verified.close()
        if wrapper_fd >= 0:
            os.close(wrapper_fd)
    out = (proc.stdout or "") + (proc.stderr or "")
    # The canon contract applies to script stdout; the exit code stays the
    # observed fact, result_status is the contract verdict.
    result_status = (
        # equivalent: a falsy stdout and the "XXXX" sentinel both lack a JSON
        # claim line, so evaluate_script_result returns not_evaluated either way.
        evaluate_script_result(proc.stdout or "", proc.returncode)
        if plan.script_type == "python"
        else RESULT_NOT_EVALUATED
    )
    return PlanRunResult(
        proc.returncode,
        # The observed output only — never padded with the invocation
        # description: an empty run result must stay empty so the evaluator
        # gate sees what the process actually produced.
        out,
        started=True,
        result_status=result_status,
    )


def _filter_tool_output(output: str) -> str:
    return filter_tool_output(output)


def _plan_started(run: PlanRunResult | tuple[int, str]) -> bool:
    """Whether the executor started; a plain tuple never observed it."""
    return getattr(run, "started", False)


def _plan_result_status(run: PlanRunResult | tuple[int, str]) -> str:
    """The contract verdict; a plain tuple was never evaluated."""
    return getattr(run, "result_status", RESULT_NOT_EVALUATED)


def _tool_output_weak(output: str, exit_code: int) -> bool:
    filtered = _filter_tool_output(output)
    if not filtered:
        return True
    if exit_code not in (0, 1):
        return True
    return False


def _infer_rag_domains(task: str) -> list[str] | None:
    text = task.lower()
    domains: list[str] = []
    if any(
        token in text
        for token in (
            "quality gate",
            "allure dashboard",
            "analytics grid",
            "sparkline",
            "allure agent",
            "metrics catalog",
            "chart matrix",
            "allure shell",
            "analytics index",
        )
    ):
        domains.append("analytics")
    if any(
        token in text
        for token in (
            "page object",
            "po locator",
            "selenide",
            "test pyramid",
            "test layer",
            "ci workflow",
            "allurerc",
        )
    ):
        domains.append("testing")
    if any(
        token in text
        for token in (
            "testconfig",
            "test config",
            "baseurl",
            "base url",
            "healthcheck",
            "configurator",
            "-d flag",
            "property override",
        )
    ):
        domains.append("config")
    if any(token in text for token in ("stack", "openapi", "spring", "flows/login")):
        domains.append("stacks")
    return domains or None


def _rag_fallback_output(
    task: str, root: Path, *, invocation: ProductInvocation | None = None,
) -> str | None:
    def search(domains):
        if invocation is None:
            return search_rag(task, root, domains=domains, limit=5)
        return invocation.dispatch(
            lambda: search_rag(task, root, domains=domains, limit=5), cause="fallback",
        )

    domains = _infer_rag_domains(task)
    hits = search(domains)
    if not hits and (domains is not None or invocation is None):
        # equivalent: domains defaults to None — dropping the kwarg is the same call.
        hits = search(None)  # pragma: no mutate
    if not hits:
        return None
    return format_hits(task, hits)


def execute_task(
    task: str,
    root: Path | None = None,
    *,
    decision: RouteDecision | None = None,
    plan: RunPlan | None = None,
    invocation: ProductInvocation | None = None,
    request_id: str | None = None,
    input_version: str | int | None = None,
) -> TaskRunResult:
    root = root or find_workspace_root()
    if invocation is None:
        return _execute_task(task, root, decision=decision, plan=plan)
    decision, plan = deepcopy((decision, plan))
    return invocation.run(
        lambda: _execute_task(task, root, decision=decision, plan=plan, invocation=invocation),
        root=root, op="execute_task", params=(task, decision, plan),
        request_id=request_id, input_version=input_version,
    )


def _execute_task(
    task: str,
    root: Path,
    *,
    decision: RouteDecision | None = None,
    plan: RunPlan | None = None,
    invocation: ProductInvocation | None = None,
) -> TaskRunResult:
    root = root or find_workspace_root()
    # cmd_run already routed/planned once — reuse its artifacts so one
    # operation never plans twice (a rag plan re-runs search_rag otherwise).
    decision = decision if decision is not None else route_task(task, root)
    plan = plan if plan is not None else plan_run(decision, task, root)

    def run_plan():
        if invocation is None:
            return execute_plan(plan)
        return invocation.dispatch(
            lambda: execute_plan(plan, invocation=invocation),
            cause="executor",
        )

    def fallback():
        if invocation is None:
            out = _rag_fallback_output(task, root)
        else:
            out = _rag_fallback_output(task, root, invocation=invocation)
        # The tier handoff is real once the fallback search dispatched —
        # record it even when RAG returns nothing.
        from greedy_token.cheap_llm import observe_transition

        observe_transition("tool->rag", request_kind="dynamic_fallback")
        return out

    if decision.target == "cursor":
        return TaskRunResult(
            decision=decision,
            output=(
                "Refusing --execute: cursor tier requires expensive LLM (Agent chat).\n"
                f"{plan.dry_run_output}"
            ),
            exit_code=1,
        )

    if plan.executable and plan.command:
        run = run_plan()
        code, out = run
        started = _plan_started(run)
        if decision.target == "tool":
            filtered = _filter_tool_output(out)
            if _tool_output_weak(out, code):
                rag_out = fallback()
                if rag_out:
                    note = (
                        f"rg: no useful matches for «{_extract_query_note(task, root)}» "
                        f"→ fallback RAG\n\n"
                    )
                    return TaskRunResult(
                        decision=decision,
                        output=note + rag_out,
                        used_rag_fallback=True,
                        started=started,
                        # exit_code stays at the dataclass default 0.
                    )
                if _observed_armed() and code == 1 and not out.strip():
                    # The python backend completed a zero-hit search: report
                    # the same miss verdict greedy_token_search emits rather
                    # than a bare non-zero process code.
                    query, operands, _limit = _rg_argv_tail(plan.argv or ())
                    scope = ", ".join(operands) or "workspace"
                    return TaskRunResult(
                        decision=decision,
                        output=f"No matches for {query!r} in {scope}.",
                        exit_code=0,
                        started=started,
                    )
                return TaskRunResult(
                    decision=decision,
                    output=cap_tool_output(out.strip()),
                    exit_code=code,
                    started=started,
                )
            shown = cap_tool_output(filtered)
            if filtered != out.strip():
                note = f"rg (without agent-internal dirs):\n{shown}\n"
                rag_out = fallback()
                if rag_out and len(filtered.splitlines()) < 3:
                    note += f"\n---\nAdditional RAG:\n\n{rag_out}"
                    return TaskRunResult(
                        decision=decision,
                        output=note,
                        used_rag_fallback=True,
                        started=started,
                        # exit_code stays at the dataclass default 0.
                    )
                return TaskRunResult(
                    decision=decision, output=note, exit_code=code, started=started
                )
            return TaskRunResult(
                decision=decision, output=shown, exit_code=code, started=started
            )

        return TaskRunResult(
            decision=decision,
            output=out,
            exit_code=code,
            started=started,
            result_status=_plan_result_status(run),
        )

    run = run_plan()
    code, out = run
    return TaskRunResult(
        decision=decision,
        output=out,
        exit_code=code,
        started=_plan_started(run),
        result_status=_plan_result_status(run),
    )


def _extract_query_note(task: str, root: Path | None = None) -> str:
    """Content pattern for the fallback note — path tokens became rg scope."""
    from greedy_token.router import _extract_search_query, _extract_search_targets

    pattern, _scopes = _extract_search_targets(task, root or find_workspace_root())
    return pattern or _extract_search_query(task)


def task_result_gate(result: TaskRunResult, decision: RouteDecision) -> GateDecision:
    """Apply the evaluator gate to an executed task result.

    Every outward-facing consumer (CLI, hook, MCP) must route the "is this an
    answer / may it claim savings" decision through the gate instead of
    re-reading ``started``/``exit_code``/``result_status`` locally.  The tool
    tier keeps its own usefulness evaluator — filtered output plus the rg
    exit-code vocabulary — which the gate honours via ``output_useful``;
    every other tier's native evaluator is the observed output itself:
    nothing on stdout/stderr means nothing was delivered.
    """
    # equivalent: the initial value only reaches the gate as output_useful
    # when result.started is False, and the not-started gate return ignores
    # output_useful — "" and None yield the same GateDecision.
    useful = None  # pragma: no mutate
    if result.started:
        if decision.target == "tool":
            useful = not _tool_output_weak(result.output, result.exit_code)
        else:
            useful = bool(result.output.strip())
    return evaluate_result_gate(
        started=result.started,
        result_status=result.result_status,
        tier=decision.target,
        ok=result.exit_code == 0,
        output_useful=useful,
    )
