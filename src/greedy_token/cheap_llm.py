"""Health and chat adapters for cheap LLM providers (Ollama native, OpenAI-compatible)."""

from __future__ import annotations

import atexit
import base64
import contextvars
import hashlib
import json
import os
import runpy
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from greedy_token.settings import CheapLlmSettings

CHEAP_LLM_PROBE_TTL = 3.0
_LOOPBACK_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_cheap_llm_probe_cache: dict[str, tuple[float, CheapLlmProbe]] = {}

# ---------------------------------------------------------------------------
# Coverage-aware observation ledger (evidence benchmark D1).
#
# The ledger is an independent JSONL channel — not stdout, not the footer,
# not usage.jsonl and not spend.jsonl. A driver bootstraps the file, passes
# GREEDY_TOKEN_OBSERVE* to a child and the child installs guards before the
# product entrypoint runs. Observed scope is python_provider: in-process
# provider intents, pre-network dispatch denials and denied opaque launches.
# ---------------------------------------------------------------------------

OBSERVE_LEDGER_ENV = "GREEDY_TOKEN_OBSERVE"
OBSERVE_RUN_ENV = "GREEDY_TOKEN_OBSERVE_RUN"
OBSERVE_CASE_ENV = "GREEDY_TOKEN_OBSERVE_CASE"
OBSERVE_HTTP_ALLOW_ENV = "GREEDY_TOKEN_OBSERVE_HTTP_ALLOW"
OBSERVE_ADMISSION_ENV = "GREEDY_TOKEN_OBSERVE_ADMISSION"
OBSERVATION_SCOPE = "python_provider"

# Health-probe paths of the declared fixture origins — the only IO admitted
# inside an observed run. Everything else is denied before the network.
_HTTP_ALLOW_PATHS = frozenset({"/api/tags", "/v1/models"})

OBSERVED_MODULE_ENTRY = (
    "from greedy_token.cheap_llm import run_observed_module as _r;_r()"
)
OBSERVED_EXEC_ENTRY = "from greedy_token.cheap_llm import run_observed_exec as _r;_r()"


class DispatchDeniedError(OSError):
    """An observed run refused an action before it could escape coverage."""


_observe_seq = 0
_observe_lock = threading.Lock()
_observe_installed = False
# Causal binding is per execution context (thread/task) — a module-global
# would let one thread's attempt be claimed by another thread's dispatch.
_observe_attempt_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "greedy_token_observe_attempt", default=""
)
_popen_init_orig = None
_opener_open_orig = None
_os_system_orig = None
_posix_spawn_orig = None
_posix_spawnp_orig = None
# Registered expectations for controlled observed children. A parent
# registers one admission per spawn; the native guard consumes it once and
# the coverage validator joins the child's own binding event back to it.
_child_admissions: dict[str, dict] = {}


def observation_armed() -> bool:
    return bool(os.environ.get(OBSERVE_LEDGER_ENV, "").strip())


def observe_ledger_write(path: str, event: dict) -> None:
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _observe_next_seq() -> int:
    global _observe_seq
    with _observe_lock:
        _observe_seq += 1
        return _observe_seq


def _observe_emit(kind: str, seq: int | None = None, **fields) -> dict | None:
    path = os.environ.get(OBSERVE_LEDGER_ENV, "").strip()
    if not path:
        if _observe_installed:
            raise DispatchDeniedError(
                "observed run: ledger channel lost — refusing "
                "unobserved execution"
            )
        return None
    event = {
        "kind": kind,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "seq": seq if seq is not None else _observe_next_seq(),
        "run_id": os.environ.get(OBSERVE_RUN_ENV, ""),
        "case_id": os.environ.get(OBSERVE_CASE_ENV, ""),
        "thread_ident": threading.get_ident(),
        "thread_name": threading.current_thread().name,
        "monotonic": round(time.monotonic(), 6),
        "epoch": round(time.time(), 6),
        **fields,
    }
    try:
        observe_ledger_write(path, event)
    except OSError as exc:
        if _observe_installed:
            raise DispatchDeniedError(
                "observed run: ledger write failed — refusing "
                "unobserved execution"
            ) from exc
        return None
    return event


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def observe_model_attempt(
    *,
    profile: str,
    model_id: str,
    provider: str,
    billing: str,
    index: int,
    cause: str = "candidate",
    parent_attempt_id: str = "",
    detail: str = "",
) -> str:
    """Record model intent before any spend guard can spend or refuse.

    The first leaf binds to its candidate; retries and hidden provider
    fallbacks are separate events with a causal parent — never folded into
    one attempt and never counted from candidate-list length.
    """
    seq = _observe_next_seq()
    attempt_id = f"{os.getpid()}-{seq}"
    event = _observe_emit(
        "model_attempt",
        seq=seq,
        attempt_id=attempt_id,
        profile=profile,
        model_id=model_id,
        provider=provider,
        billing=billing,
        candidate_index=index,
        cause=cause,
        parent_attempt_id=parent_attempt_id,
        detail=detail,
    )
    if event is None:
        return ""
    _observe_attempt_ctx.set(attempt_id)
    return attempt_id


def observe_provider_fallback(provider: str, detail: str) -> str:
    """Hidden in-call provider switch is a new causal leaf, not a retry-free hop."""
    return observe_model_attempt(
        profile="",
        model_id="",
        provider=provider,
        billing="",
        index=-1,
        cause="provider_fallback",
        parent_attempt_id=_observe_attempt_ctx.get(),
        detail=detail,
    )


def observe_set_attempt(attempt_id: str) -> None:
    _observe_attempt_ctx.set(attempt_id)


def observe_provider_dispatch(provider: str, endpoint: str) -> None:
    """Deny real model dispatch before the network in an observed run."""
    if not observation_armed() and not _observe_installed:
        return
    attempt_id = _observe_attempt_ctx.get()
    if not attempt_id:
        # A provider call that bypassed candidate registration is still a
        # model attempt: it registers its own causal leaf before the
        # denial so the ledger can never show a zero-attempt dispatch.
        attempt_id = observe_model_attempt(
            profile="",
            model_id="",
            provider=provider,
            billing="",
            index=-1,
            cause="direct_dispatch",
            detail=f"dispatch without registered candidate ({endpoint})",
        )
    _observe_emit(
        "llm_request",
        provider=provider,
        endpoint=endpoint,
        attempt_id=attempt_id,
        decision="denied",
        reason="observed_run_model_dispatch_blocked",
    )
    raise DispatchDeniedError(
        f"observed run: {endpoint} model dispatch denied before network"
    )


def observe_provider_probe(endpoint: str) -> None:
    """Semantic record for read-only provider health probes."""
    if observation_armed() or _observe_installed:
        _observe_emit("provider_probe", endpoint=endpoint)


def _env_sha256(env: dict) -> str:
    items = sorted((str(k), str(v)) for k, v in env.items())
    return _sha256_text("\x00".join(f"{k}={v}" for k, v in items))


def new_child_admission_id() -> str:
    import uuid

    # No ledger seq is consumed here: ids join admission→launch→bind, the
    # per-pid event sequence must stay contiguous with no burned numbers.
    return f"{os.getpid()}-{uuid.uuid4().hex}"


def admit_trusted_child(
    *,
    admission_id: str,
    argv: list[str],
    env: dict,
    pass_fds: tuple[int, ...],
    cwd: str,
    runner_sha256: str,
    source_sha256: str,
    source_bytes: int,
    script_path: str,
    authority: str,
    fd_device: int,
    fd_inode: int,
) -> None:
    """Register parent-side expectations for one controlled observed child.

    The admission is consumed once by the native guard at Popen init and is
    only joined to evidence when the child's own bind event independently
    reports the same source/FD/runner/argv/env identity.
    """
    argv_repr = "\x00".join(str(item) for item in argv)
    record = {
        "admission_id": admission_id,
        "argv_sha256": _sha256_text(argv_repr),
        "child_argv_sha256": _sha256_text("\x00".join(str(a) for a in argv[1:])),
        "env_sha256": _env_sha256(env),
        "pass_fds": list(pass_fds),
        "cwd": str(cwd),
        "runner_sha256": runner_sha256,
        "source_sha256": source_sha256,
        "source_bytes": source_bytes,
        "script_path": script_path,
        "authority": authority,
        "fd_device": fd_device,
        "fd_inode": fd_inode,
    }
    with _observe_lock:
        _child_admissions[admission_id] = record
    _observe_emit("trusted_child_admission", **record)


def _match_child_admission(spec, kwargs) -> str | None:
    """Consume a registered admission iff the spawn still matches it."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    argv_repr = "\x00".join(str(item) for item in spec)
    env = kwargs.get("env")
    if env is None:
        return None
    pass_fds = tuple(sorted(kwargs.get("pass_fds") or ()))
    cwd = kwargs.get("cwd")
    with _observe_lock:
        for admission_id, record in list(_child_admissions.items()):
            if _sha256_text(argv_repr) != record["argv_sha256"]:
                continue
            if _env_sha256(env) != record["env_sha256"]:
                continue
            if pass_fds != tuple(sorted(record["pass_fds"])):
                continue
            if str(cwd or "") != record["cwd"]:
                continue
            del _child_admissions[admission_id]
            return admission_id
    return None


def observe_trusted_child_bind(
    *,
    source: bytes,
    script_path: str,
    fd_identity: tuple[int, int],
    runner_path: str,
) -> None:
    """Child-side binding: hash the consumed source buffer, not a path read.

    Emitted by the trusted runner after it read the script bytes from the
    verified FD it will pass to compile — the coverage validator joins this
    event to the parent's independent admission record.
    """
    if not _observe_installed:
        return
    runner_sha256 = ""
    try:
        with open(runner_path, "rb") as handle:
            runner_sha256 = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        runner_sha256 = ""
    st_dev, st_ino = fd_identity
    _observe_emit(
        "trusted_child_bind",
        admission_id=os.environ.get(OBSERVE_ADMISSION_ENV, ""),
        source_sha256=hashlib.sha256(source).hexdigest(),
        source_bytes=len(source),
        script_path=script_path,
        runner_sha256=runner_sha256,
        env_sha256=_env_sha256(dict(os.environ)),
        fd_device=st_dev,
        fd_inode=st_ino,
    )


def observe_resource_probe(probe: str) -> bool:
    """Whether a host resource probe may run under observation.

    Observed runs skip hardware/doctor probes entirely: the probe's native
    calls (sysctl/system_profiler) would be denied anyway, and fabricating a
    measurement is worse than recording the skip on the ledger.
    """
    if not observation_armed() and not _observe_installed:
        return True
    _observe_emit("resource_probe", probe=probe, decision="skipped")
    return False


def observe_transition(transition: str, *, request_kind: str, **fields) -> None:
    """Bound executor handoff: dynamic fallback, pipeline sequence, route."""
    if not observation_armed() and not _observe_installed:
        return
    _observe_emit(
        "transition",
        transition=transition,
        request_kind=request_kind,
        **fields,
    )


def observe_search_backend(
    *, engine: str, scope: str, hit_count: int | None = None, native: str
) -> None:
    """Record which search backend answered before any native launch."""
    if not observation_armed() and not _observe_installed:
        return
    _observe_emit(
        "search_backend",
        engine=engine,
        scope=scope,
        hit_count=hit_count,
        native=native,
    )


def _native_launch_guard(self, *args, **kwargs) -> None:
    self._child_created = False  # keep Popen.__del__ safe on denied init
    spec = args[0] if args else kwargs.get("args")
    if isinstance(spec, (list, tuple)) and spec:
        argv0 = str(spec[0])
        argv_repr = "\x00".join(str(item) for item in spec)
    else:
        argv0 = str(spec) if spec is not None else ""
        argv_repr = str(spec)
    admission_id = _match_child_admission(spec, kwargs)
    if admission_id is not None:
        _observe_emit(
            "native_launch",
            argv0=os.path.basename(argv0) or argv0,
            argv_sha256=_sha256_text(argv_repr),
            decision="admitted",
            reason="trusted_runner_admission",
            admission_id=admission_id,
        )
        _popen_init_orig(self, *args, **kwargs)
        return
    _observe_emit(
        "native_launch",
        argv0=os.path.basename(argv0) or argv0,
        argv_sha256=_sha256_text(argv_repr),
        decision="denied",
        reason="unsupported_native_coverage",
    )
    raise DispatchDeniedError(
        f"observed run: native launch denied ({os.path.basename(argv0) or argv0!r})"
    )


def _os_system_guard(*_args, **_kwargs):
    _observe_emit(
        "native_launch",
        argv0="os.system",
        argv_sha256=_sha256_text(repr(_args)),
        decision="denied",
        reason="unsupported_native_coverage",
    )
    raise DispatchDeniedError("observed run: os.system denied")


def _posix_spawn_guard(*_args, **_kwargs):
    _observe_emit(
        "native_launch",
        argv0="posix_spawn",
        argv_sha256=_sha256_text(repr(_args)),
        decision="denied",
        reason="unsupported_native_coverage",
    )
    raise DispatchDeniedError("observed run: posix_spawn denied")


def _http_admission() -> set[tuple[str, str]]:
    """Declared fixture health origins — (scheme, netloc) pairs only."""
    raw = os.environ.get(OBSERVE_HTTP_ALLOW_ENV, "")
    origins: set[tuple[str, str]] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = urllib.parse.urlsplit(item)
        if parts.scheme and parts.netloc:
            origins.add((parts.scheme.lower(), parts.netloc.lower()))
    return origins


def _opener_open_guard(self, fullurl, *args, **kwargs):
    """Admission gate on OpenerDirector.open — covers urlopen, custom
    openers and every redirect hop, not only the first request."""
    if isinstance(fullurl, urllib.request.Request):
        target = fullurl.full_url
        method = fullurl.get_method()
    else:
        target = str(fullurl)
        data = args[0] if args else kwargs.get("data")
        method = "POST" if data is not None else "GET"
    parts = urllib.parse.urlsplit(target)
    admitted = (
        method in ("GET", "HEAD")
        and (parts.scheme.lower(), parts.netloc.lower()) in _http_admission()
        and parts.path in _HTTP_ALLOW_PATHS
    )
    _observe_emit(
        "io_http",
        method=method,
        scheme=parts.scheme,
        netloc=parts.netloc,
        path=parts.path,
        attempt_id=_observe_attempt_ctx.get(),
        decision="allowed" if admitted else "denied",
        reason="" if admitted else "observed_run_io_restricted",
    )
    if not admitted:
        raise DispatchDeniedError(
            f"observed run: {method} {target!r} denied before network"
        )
    return _opener_open_orig(self, fullurl, *args, **kwargs)


def install_observation() -> None:
    """Arm guards and open the child segment of the ledger. Fails closed."""
    global _observe_installed, _popen_init_orig, _opener_open_orig
    global _os_system_orig, _posix_spawn_orig, _posix_spawnp_orig
    if _observe_installed:
        return
    _observe_installed = True
    path = os.environ.get(OBSERVE_LEDGER_ENV, "").strip()
    if not path or not os.path.isfile(path):
        sys.stderr.write(
            "greedy-token: observed run requested but ledger is missing; "
            "refusing unobserved execution\n"
        )
        raise SystemExit(3)
    _observe_emit(
        "child_start",
        argv_sha256=_sha256_text("\x00".join(sys.argv)),
        argc=len(sys.argv),
        exe=os.path.basename(sys.executable),
        boundary="interpreter_start_pre_product_import",
    )
    _popen_init_orig = subprocess.Popen.__init__
    subprocess.Popen.__init__ = _native_launch_guard
    _os_system_orig = os.system
    os.system = _os_system_guard
    if hasattr(os, "posix_spawn"):
        _posix_spawn_orig = os.posix_spawn
        os.posix_spawn = _posix_spawn_guard
    if hasattr(os, "posix_spawnp"):
        _posix_spawnp_orig = os.posix_spawnp
        os.posix_spawnp = _posix_spawn_guard
    _opener_open_orig = urllib.request.OpenerDirector.open
    urllib.request.OpenerDirector.open = _opener_open_guard
    atexit.register(_observed_child_exit)


def _observed_child_exit() -> None:
    _observe_emit("child_exit")


def run_observed_module() -> None:
    """`python -c OBSERVED_MODULE_ENTRY <module> [args]` — bootstrap first."""
    install_observation()
    if len(sys.argv) < 2:
        raise SystemExit("observed module entry requires a module name")
    module = sys.argv[1]
    sys.argv = [module] + sys.argv[2:]
    runpy.run_module(module, run_name="__main__")


def run_observed_exec() -> None:
    """`python -c OBSERVED_EXEC_ENTRY <code>` — bootstrap first."""
    install_observation()
    if len(sys.argv) < 2:
        raise SystemExit("observed exec entry requires a code argument")
    code = sys.argv[1]
    sys.argv = ["observed-exec"] + sys.argv[2:]
    exec(compile(code, "<observed-exec>", "exec"), {"__name__": "__main__"})


class MalformedResponseError(ValueError):
    """The provider answered with a payload that does not fit its API shape.

    Unlike a transport error the HTTP exchange completed — tokens may already
    be billed — so the invoke path records the attempt (and its cost) as a
    structured failure instead of crashing on IndexError/KeyError.  Any usage
    counts the response did carry ride along for spend accounting.
    """

    def __init__(self, message: str, *, eval_tokens: int | None = None) -> None:
        super().__init__(message)
        self.eval_tokens = eval_tokens


@dataclass(frozen=True)
class CheapLlmProbe:
    """Outcome of a cheap-LLM health probe.

    `reachable` answers "did the runtime respond"; `model_present` answers "is
    the configured model actually served". A runtime that is up with the wrong
    model configured used to look healthy here and fail later at call time.
    `model_present` is None when the answer could not be determined.
    """

    reachable: bool
    model_present: bool | None
    models: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.reachable and self.model_present is not False


def _cache_key(settings: CheapLlmSettings) -> str:
    return f"{settings.provider}:{settings.url.rstrip('/')}:{settings.model}"


def openai_compat_base(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def clear_cheap_llm_probe_cache() -> None:
    _cheap_llm_probe_cache.clear()


def split_userinfo(url: str) -> tuple[str, tuple[str, str] | None]:
    """Split `scheme://user:pass@host/path` into a clean URL and credentials.

    urllib will not send Basic credentials embedded in a URL on its own, and
    leaving the userinfo in place leaks it into the request line.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.username:
        return url, None
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    clean = urllib.parse.urlunsplit(
        (parts.scheme, host, parts.path, parts.query, parts.fragment)
    )
    return clean, (parts.username, parts.password or "")


def _is_loopback(url: str) -> bool:
    return (urllib.parse.urlsplit(url).hostname or "").lower() in _LOOPBACK_HOSTS


def _env_credentials() -> tuple[str, str] | None:
    for user_var, password_var in (
        ("CHEAP_LLM_USER", "CHEAP_LLM_PASSWORD"),
        ("OLLAMA_USER", "OLLAMA_PASSWORD"),
    ):
        user = os.environ.get(user_var, "").strip()
        if user:
            return user, os.environ.get(password_var, "")
    return None


def request_target(settings: CheapLlmSettings) -> tuple[str, dict[str, str]]:
    """Base URL with userinfo stripped, plus the auth headers it needs."""
    url, creds = split_userinfo(settings.url)
    return url.rstrip("/"), _auth_headers(settings, url=url, creds=creds)


def _auth_headers(
    settings: CheapLlmSettings,
    *,
    url: str | None = None,
    creds: tuple[str, str] | None = None,
) -> dict[str, str]:
    if settings.provider == "openai_compat" and settings.api_key:
        return {"Authorization": f"Bearer {settings.api_key}"}
    target = url if url is not None else split_userinfo(settings.url)[0]
    if _is_loopback(target):
        return {}
    if creds is None:
        creds = split_userinfo(settings.url)[1] or _env_credentials()
    if creds is None:
        return {}
    token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def cheap_llm_available(settings: CheapLlmSettings, timeout: float = 2.0) -> bool:
    return probe_cheap_llm(settings, timeout=timeout).ok


def probe_cheap_llm(settings: CheapLlmSettings, timeout: float = 2.0) -> CheapLlmProbe:
    key = _cache_key(settings)
    now = time.monotonic()
    cached = _cheap_llm_probe_cache.get(key)
    if cached is not None and now - cached[0] < CHEAP_LLM_PROBE_TTL:
        return cached[1]

    probe = _probe_health(settings, timeout=timeout)
    _cheap_llm_probe_cache[key] = (now, probe)
    return probe


def _probe_health(settings: CheapLlmSettings, *, timeout: float) -> CheapLlmProbe:
    base, headers = request_target(settings)
    if settings.provider == "openai_compat":
        url = f"{openai_compat_base(base)}/models"
    else:
        url = f"{base}/api/tags"
    observe_provider_probe(f"{settings.provider}_health")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        reason = f"HTTP {exc.code}"
        if exc.code in (401, 403):
            reason += (
                " — set CHEAP_LLM_USER / CHEAP_LLM_PASSWORD "
                "(or OLLAMA_USER / OLLAMA_PASSWORD)"
            )
        return CheapLlmProbe(reachable=False, model_present=None, reason=reason)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        return CheapLlmProbe(
            reachable=False, model_present=None, reason=str(exc) or "unreachable"
        )

    served = served_models(data)
    if not served:
        return CheapLlmProbe(reachable=True, model_present=False, reason="no models served")
    present = model_is_served(settings.model, served)
    reason = "" if present else f"model {settings.model!r} not served"
    return CheapLlmProbe(
        reachable=True, model_present=present, models=served, reason=reason
    )


def served_models(payload: object) -> tuple[str, ...]:
    """Model names from Ollama `/api/tags` or an OpenAI-compatible `/v1/models`."""
    if not isinstance(payload, dict):
        return ()
    entries = payload.get("models")
    if entries is None:
        entries = payload.get("data")
    names = [
        str(entry.get("name") or entry.get("id") or "").strip()
        for entry in (entries or [])
        if isinstance(entry, dict)
    ]
    return tuple(name for name in names if name)


def model_is_served(model: str, served: tuple[str, ...]) -> bool:
    """Ollama reports `name:tag`; an untagged config means the `latest` tag."""
    wanted = model.strip()
    if not wanted:
        return False
    candidates = {wanted, f"{wanted}:latest"}
    return any(
        name in candidates or name.removesuffix(":latest") == wanted for name in served
    )


def cheap_llm_chat(
    settings: CheapLlmSettings,
    *,
    system: str,
    user: str,
    timeout: float = 120.0,
) -> tuple[str, int | None]:
    if settings.provider == "openai_compat":
        return _chat_openai_compat(settings, system=system, user=user, timeout=timeout)
    return _chat_ollama(settings, system=system, user=user, timeout=timeout)


def _chat_ollama(
    settings: CheapLlmSettings,
    *,
    system: str,
    user: str,
    timeout: float,
) -> tuple[str, int | None]:
    observe_provider_dispatch("ollama", "ollama_chat")
    url, auth = request_target(settings)
    body = json.dumps(
        {
            "model": settings.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json", **auth},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    eval_tokens = data.get("eval_count") if isinstance(data, dict) else None
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise MalformedResponseError(
            "ollama response has no message content", eval_tokens=eval_tokens
        )
    return content.strip(), eval_tokens


def _chat_openai_compat(
    settings: CheapLlmSettings,
    *,
    system: str,
    user: str,
    timeout: float,
) -> tuple[str, int | None]:
    observe_provider_dispatch("openai_compat", "openai_compat")
    target, auth = request_target(settings)
    base = openai_compat_base(target)
    body = json.dumps(
        {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    headers = {"Content-Type": "application/json", **auth}
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    usage = data.get("usage") if isinstance(data, dict) else None
    if usage is not None and not isinstance(usage, dict):
        raise MalformedResponseError("openai_compat response usage is not an object")
    eval_tokens = (usage or {}).get("completion_tokens")
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise MalformedResponseError(
            "openai_compat response has no choices", eval_tokens=eval_tokens
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise MalformedResponseError(
            "openai_compat response has no message content", eval_tokens=eval_tokens
        )
    return content.strip(), eval_tokens


def cheap_llm_status_line(settings: CheapLlmSettings) -> str:
    provider = settings.provider
    url = settings.url
    model = settings.model
    probe = probe_cheap_llm(settings)
    if probe.ok:
        return f"Cheap LLM: available ({provider}, {url}, model={model})"
    if probe.reachable:
        served = ", ".join(probe.models) or "none"
        return (
            f"Cheap LLM: model unavailable ({provider}, {url}, model={model}) — "
            f"{probe.reason}; served: {served}"
        )
    return (
        f"Cheap LLM: unavailable ({provider}, {url}) — "
        f"{probe.reason or 'start runtime'} — or use expensive LLM (Cursor)"
    )
