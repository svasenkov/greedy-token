"""Coverage-gap tests for the observation ledger subsystem in cheap_llm.

The observed-run machinery normally executes inside guarded child processes;
these tests exercise the same boundaries in-process: the ledger emit path,
attempt binding, provider denials, child admissions, native/HTTP launch
guards and the observed entrypoints.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock

import pytest

import allure
from greedy_token import cheap_llm
from greedy_token.cheap_llm import DispatchDeniedError

pytestmark = [
    allure.epic("Observation"),
    allure.parent_suite("Observation"),
    allure.feature("Observation ledger"),
    allure.suite("Cheap LLM observation gaps"),
]


def _events(ledger: Path) -> list[dict]:
    if not ledger.is_file():
        return []
    return [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def ledger_env(monkeypatch, tmp_path):
    """Armed observation channel with isolated per-test module state."""
    ledger = tmp_path / "observed.jsonl"
    monkeypatch.setenv(cheap_llm.OBSERVE_LEDGER_ENV, str(ledger))
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    monkeypatch.setattr(cheap_llm, "_child_admissions", {})
    cheap_llm._observe_attempt_ctx.set("")
    yield ledger
    cheap_llm._observe_attempt_ctx.set("")


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """Real install_observation with full teardown of the patched builtins."""
    ledger = tmp_path / "observed.jsonl"
    ledger.write_text("", encoding="utf-8")
    monkeypatch.setenv(cheap_llm.OBSERVE_LEDGER_ENV, str(ledger))
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    monkeypatch.setattr(cheap_llm, "_child_admissions", {})
    saved = (
        subprocess.Popen.__init__,
        os.system,
        urllib.request.OpenerDirector.open,
        getattr(os, "posix_spawn", None),
        getattr(os, "posix_spawnp", None),
    )
    try:
        cheap_llm.install_observation()
        yield ledger
    finally:
        (
            subprocess.Popen.__init__,
            os.system,
            urllib.request.OpenerDirector.open,
        ) = saved[:3]
        if saved[3] is not None:
            os.posix_spawn = saved[3]
        if saved[4] is not None:
            os.posix_spawnp = saved[4]
        cheap_llm._observe_installed = False
        cheap_llm._popen_init_orig = None
        cheap_llm._opener_open_orig = None
        cheap_llm._os_system_orig = None
        cheap_llm._posix_spawn_orig = None
        cheap_llm._posix_spawnp_orig = None
        cheap_llm._observe_attempt_ctx.set("")


# ---------------------------------------------------------------------------
# Ledger channel: _observe_emit / observe_ledger_write
# ---------------------------------------------------------------------------


@allure.title("armed ledger records the event with run/case/thread fields")
def test_observe_emit_writes_event(ledger_env, monkeypatch):
    monkeypatch.setenv(cheap_llm.OBSERVE_RUN_ENV, "run-1")
    monkeypatch.setenv(cheap_llm.OBSERVE_CASE_ENV, "case-7")
    event = cheap_llm._observe_emit("probe_kind", seq=41, extra="x")
    assert event["kind"] == "probe_kind"
    assert event["seq"] == 41
    assert event["run_id"] == "run-1"
    assert event["case_id"] == "case-7"
    assert event["pid"] == os.getpid()
    rows = _events(ledger_env)
    assert rows[-1]["kind"] == "probe_kind"
    assert rows[-1]["extra"] == "x"


@allure.title("unarmed emit without guards is a silent no-op")
def test_observe_emit_unarmed_returns_none(monkeypatch):
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV, raising=False)
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    assert cheap_llm._observe_emit("probe_kind") is None


@allure.title("installed run without a ledger channel fails closed")
def test_observe_emit_missing_ledger_fails_closed(monkeypatch):
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV, raising=False)
    monkeypatch.setattr(cheap_llm, "_observe_installed", True)
    with pytest.raises(DispatchDeniedError, match="ledger channel lost"):
        cheap_llm._observe_emit("probe_kind")


@allure.title("installed run with an unwritable ledger refuses to continue")
def test_observe_emit_unwritable_ledger_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(
        cheap_llm.OBSERVE_LEDGER_ENV, str(tmp_path / "missing-dir" / "l.jsonl")
    )
    monkeypatch.setattr(cheap_llm, "_observe_installed", True)
    with pytest.raises(DispatchDeniedError, match="ledger write failed"):
        cheap_llm._observe_emit("probe_kind")


@allure.title("uninstalled run with an unwritable ledger degrades to None")
def test_observe_emit_unwritable_ledger_uninstalled(monkeypatch, tmp_path):
    monkeypatch.setenv(
        cheap_llm.OBSERVE_LEDGER_ENV, str(tmp_path / "missing-dir" / "l.jsonl")
    )
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    assert cheap_llm._observe_emit("probe_kind") is None


# ---------------------------------------------------------------------------
# Attempt binding: observe_model_attempt / fallback / dispatch / probe
# ---------------------------------------------------------------------------


@allure.title("model attempt binds the attempt id to the execution context")
def test_observe_model_attempt_binds_context(ledger_env):
    attempt = cheap_llm.observe_model_attempt(
        profile="cheap", model_id="m1", provider="ollama",
        billing="local", index=2,
    )
    assert attempt.startswith(f"{os.getpid()}-")
    assert cheap_llm._observe_attempt_ctx.get() == attempt
    row = _events(ledger_env)[-1]
    assert row["kind"] == "model_attempt"
    assert row["model_id"] == "m1"
    assert row["candidate_index"] == 2
    assert row["cause"] == "candidate"


@allure.title("model attempt without a ledger yields no attempt id")
def test_observe_model_attempt_unarmed_returns_empty(monkeypatch):
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV, raising=False)
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    assert cheap_llm.observe_model_attempt(
        profile="", model_id="", provider="p", billing="", index=0
    ) == ""


@allure.title("provider fallback is a causal leaf of the bound attempt")
def test_observe_provider_fallback_links_parent(ledger_env):
    cheap_llm.observe_set_attempt("parent-9")
    leaf = cheap_llm.observe_provider_fallback("yandex_gpt", "native->openai_compat")
    assert leaf and leaf != "parent-9"
    row = _events(ledger_env)[-1]
    assert row["cause"] == "provider_fallback"
    assert row["parent_attempt_id"] == "parent-9"


@allure.title("armed dispatch denial records the bound attempt on the ledger")
def test_observe_provider_dispatch_denies_with_attempt(ledger_env):
    attempt = cheap_llm.observe_model_attempt(
        profile="cheap", model_id="m", provider="ollama",
        billing="local", index=0,
    )
    with pytest.raises(DispatchDeniedError, match="dispatch denied"):
        cheap_llm.observe_provider_dispatch("ollama", "ollama_chat")
    row = _events(ledger_env)[-1]
    assert row["kind"] == "llm_request"
    assert row["decision"] == "denied"
    assert row["attempt_id"] == attempt


@allure.title("dispatch without a registered candidate emits its own leaf first")
def test_observe_provider_dispatch_registers_direct_attempt(ledger_env):
    with pytest.raises(DispatchDeniedError):
        cheap_llm.observe_provider_dispatch("openai_compat", "openai_compat")
    rows = _events(ledger_env)
    assert rows[-2]["kind"] == "model_attempt"
    assert rows[-2]["cause"] == "direct_dispatch"
    assert rows[-1]["kind"] == "llm_request"
    assert rows[-1]["attempt_id"] == rows[-2]["attempt_id"]


@allure.title("unarmed dispatch check is a pass-through")
def test_observe_provider_dispatch_unarmed_is_noop(monkeypatch):
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV, raising=False)
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    assert cheap_llm.observe_provider_dispatch("ollama", "ollama_chat") is None


@allure.title("provider probe emits only under observation")
def test_observe_provider_probe(ledger_env, monkeypatch):
    cheap_llm.observe_provider_probe("ollama_health")
    assert _events(ledger_env)[-1]["kind"] == "provider_probe"
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV)
    cheap_llm.observe_provider_probe("ollama_health")
    assert _events(ledger_env)[-1]["kind"] == "provider_probe"


# ---------------------------------------------------------------------------
# Trusted child admission: env hash, ids, admission register/match, bind
# ---------------------------------------------------------------------------


@allure.title("env hash is order-independent and content-sensitive")
def test_env_sha256_canonical():
    left = cheap_llm._env_sha256({"B": "2", "A": "1"})
    right = cheap_llm._env_sha256({"A": "1", "B": "2"})
    other = cheap_llm._env_sha256({"A": "1", "B": "3"})
    assert left == right
    assert left != other
    assert left == hashlib.sha256(b"A=1\x00B=2").hexdigest()


@allure.title("child admission ids are pid-scoped and unique")
def test_new_child_admission_id_unique():
    first, second = cheap_llm.new_child_admission_id(), cheap_llm.new_child_admission_id()
    assert first != second
    assert all(item.startswith(f"{os.getpid()}-") for item in (first, second))


def _admit(ledger_env, admission_id="adm-1", **overrides):
    record = dict(
        admission_id=admission_id,
        argv=["/bin/echo", "hello"],
        env={"PATH": "/usr/bin"},
        pass_fds=(3,),
        cwd="/work",
        runner_sha256="r",
        source_sha256="s",
        source_bytes=4,
        script_path="scripts/x.py",
        authority="manifest:scripts/x.py",
        fd_device=1,
        fd_inode=2,
    )
    record.update(overrides)
    cheap_llm.admit_trusted_child(**record)
    return record


@allure.title("admitted child registers the parent's expected identity")
def test_admit_trusted_child_registers_record(ledger_env):
    record = _admit(ledger_env)
    stored = cheap_llm._child_admissions["adm-1"]
    assert stored["script_path"] == "scripts/x.py"
    assert stored["pass_fds"] == [3]
    assert stored["argv_sha256"] == hashlib.sha256(b"/bin/echo\x00hello").hexdigest()
    assert stored["env_sha256"] == cheap_llm._env_sha256({"PATH": "/usr/bin"})
    row = _events(ledger_env)[-1]
    assert row["kind"] == "trusted_child_admission"
    assert row["admission_id"] == "adm-1"
    assert row["authority"] == record["authority"]


@allure.title("admission match consumes once and rejects every skewed field")
@pytest.mark.parametrize("skew", ["argv", "env", "pass_fds", "cwd"])
def test_match_child_admission_consumes_exact_match(ledger_env, skew):
    _admit(ledger_env, "adm-ok")
    _admit(ledger_env, "adm-other", argv=["/bin/false"])
    kwargs = {
        "env": {"PATH": "/usr/bin"},
        "pass_fds": (3,),
        "cwd": "/work",
    }
    kwargs[skew] = {"argv": ["/bin/echo", "bye"], "env": {"PATH": "/sbin"},
                    "pass_fds": (4,), "cwd": "/elsewhere"}[skew]
    argv = ["/bin/echo", "hello"] if skew != "argv" else ["/bin/echo", "bye"]
    assert cheap_llm._match_child_admission(argv, kwargs) is None
    assert cheap_llm._match_child_admission(
        ["/bin/echo", "hello"],
        {"env": {"PATH": "/usr/bin"}, "pass_fds": (3,), "cwd": "/work"},
    ) == "adm-ok"
    # Consumed once — a second identical spawn is denied.
    assert cheap_llm._match_child_admission(
        ["/bin/echo", "hello"],
        {"env": {"PATH": "/usr/bin"}, "pass_fds": (3,), "cwd": "/work"},
    ) is None
    # A non-argv spec or a spawn without env is never admissible.
    assert cheap_llm._match_child_admission("echo hello", kwargs) is None
    assert cheap_llm._match_child_admission([], kwargs) is None
    assert cheap_llm._match_child_admission(["/bin/echo", "hello"], {}) is None


@allure.title("trusted child bind reports source and fd identity on the ledger")
def test_observe_trusted_child_bind_installed(ledger_env, monkeypatch, tmp_path):
    monkeypatch.setattr(cheap_llm, "_observe_installed", True)
    runner = tmp_path / "runner.py"
    runner.write_bytes(b"runner-bytes")
    source = b"print('x')\n"
    cheap_llm.observe_trusted_child_bind(
        source=source,
        script_path="scripts/x.py",
        fd_identity=(11, 22),
        runner_path=str(runner),
    )
    row = _events(ledger_env)[-1]
    assert row["kind"] == "trusted_child_bind"
    assert row["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert row["source_bytes"] == len(source)
    assert row["runner_sha256"] == hashlib.sha256(b"runner-bytes").hexdigest()
    assert (row["fd_device"], row["fd_inode"]) == (11, 22)


@allure.title("trusted child bind degrades an unreadable runner hash")
def test_observe_trusted_child_bind_missing_runner(ledger_env, monkeypatch, tmp_path):
    monkeypatch.setattr(cheap_llm, "_observe_installed", True)
    cheap_llm.observe_trusted_child_bind(
        source=b"x", script_path="s.py",
        fd_identity=(0, 0), runner_path=str(tmp_path / "gone.py"),
    )
    assert _events(ledger_env)[-1]["runner_sha256"] == ""


@allure.title("trusted child bind without installed guards is a no-op")
def test_observe_trusted_child_bind_uninstalled(ledger_env, monkeypatch):
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    cheap_llm.observe_trusted_child_bind(
        source=b"x", script_path="s.py", fd_identity=(0, 0), runner_path="r.py",
    )
    assert _events(ledger_env) == []


# ---------------------------------------------------------------------------
# Semantic probes: resource probe / transition / search backend
# ---------------------------------------------------------------------------


@allure.title("resource probes are skipped under observation, allowed otherwise")
def test_observe_resource_probe(ledger_env, monkeypatch):
    assert cheap_llm.observe_resource_probe("run_doctor") is False
    assert _events(ledger_env)[-1]["decision"] == "skipped"
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV)
    assert cheap_llm.observe_resource_probe("run_doctor") is True


@allure.title("executor handoff transitions are recorded only when armed")
def test_observe_transition(ledger_env, monkeypatch):
    cheap_llm.observe_transition(
        "tool->rag", request_kind="dynamic_fallback", route_id="rg-1"
    )
    row = _events(ledger_env)[-1]
    assert row["transition"] == "tool->rag"
    assert row["request_kind"] == "dynamic_fallback"
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV)
    cheap_llm.observe_transition("a->b", request_kind="route_decision")
    assert _events(ledger_env)[-1]["transition"] == "tool->rag"


@allure.title("search backend choice lands on the ledger with hit count")
@pytest.mark.parametrize("hits", [None, 3])
def test_observe_search_backend(ledger_env, hits):
    cheap_llm.observe_search_backend(
        engine="python", scope="workspace", hit_count=hits, native="skipped"
    )
    row = _events(ledger_env)[-1]
    assert row["kind"] == "search_backend"
    assert row["engine"] == "python"
    assert row["hit_count"] == hits


@allure.title("unarmed search backend check is a pass-through")
def test_observe_search_backend_unarmed(monkeypatch):
    monkeypatch.delenv(cheap_llm.OBSERVE_LEDGER_ENV, raising=False)
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    cheap_llm.observe_search_backend(engine="python", scope="s", native="skipped")
    assert cheap_llm.observation_armed() is False


# ---------------------------------------------------------------------------
# Native launch and os.* guards
# ---------------------------------------------------------------------------


@allure.title("native launch guard denies unadmitted argv and string specs")
@pytest.mark.parametrize("spec", [["/bin/echo", "hi"], "echo hi", None])
def test_native_launch_guard_denies_unadmitted(ledger_env, spec):
    self = Mock()
    args = (spec,) if spec is not None else ()
    with pytest.raises(DispatchDeniedError):
        cheap_llm._native_launch_guard(self, *args)
    assert self._child_created is False
    assert _events(ledger_env)[-1]["decision"] == "denied"


@allure.title("native launch guard admits a spawn matching the parent record")
def test_native_launch_guard_admits_registered_spawn(ledger_env, monkeypatch):
    orig = Mock()
    monkeypatch.setattr(cheap_llm, "_popen_init_orig", orig)
    _admit(ledger_env, "adm-1", env={"PATH": "/usr/bin"}, cwd="/work")
    self = Mock()
    cheap_llm._native_launch_guard(
        self, ["/bin/echo", "hello"],
        env={"PATH": "/usr/bin"}, pass_fds=(3,), cwd="/work",
    )
    orig.assert_called_once()
    assert _events(ledger_env)[-1]["decision"] == "admitted"


@allure.title("os.system and posix_spawn guards deny with a ledger record")
def test_os_spawn_guards_deny(ledger_env):
    with pytest.raises(DispatchDeniedError):
        cheap_llm._os_system_guard("echo hi")
    with pytest.raises(DispatchDeniedError):
        cheap_llm._posix_spawn_guard("/bin/echo", ["/bin/echo"], {})
    rows = _events(ledger_env)
    assert [r["argv0"] for r in rows[-2:]] == ["os.system", "posix_spawn"]


# ---------------------------------------------------------------------------
# HTTP admission gate
# ---------------------------------------------------------------------------


@allure.title("http admission parses declared origins and skips junk")
def test_http_admission_parses_origins(monkeypatch):
    monkeypatch.setenv(
        cheap_llm.OBSERVE_HTTP_ALLOW_ENV,
        " http://a.test:8080 ,https://b.test,,no-scheme,http://",
    )
    assert cheap_llm._http_admission() == {
        ("http", "a.test:8080"), ("https", "b.test"),
    }


@allure.title("opener guard admits declared health GET and denies the rest")
def test_opener_open_guard(ledger_env, monkeypatch):
    monkeypatch.setenv(cheap_llm.OBSERVE_HTTP_ALLOW_ENV, "http://fix.test")
    orig = Mock(return_value="resp")
    monkeypatch.setattr(cheap_llm, "_opener_open_orig", orig)
    self = Mock()
    assert (
        cheap_llm._opener_open_guard(self, "http://fix.test/api/tags") == "resp"
    )
    request = urllib.request.Request("http://fix.test/v1/models")
    assert cheap_llm._opener_open_guard(self, request) == "resp"
    rows = _events(ledger_env)
    assert rows[-1]["decision"] == "allowed"
    assert rows[-1]["method"] == "GET"


@allure.title("opener guard denies POST, foreign origins and unknown paths")
@pytest.mark.parametrize(
    "url,kwargs",
    [
        ("http://fix.test/api/tags", {"data": b"x"}),          # POST via kwarg
        ("http://fix.test/other", {}),                          # path outside allowlist
        ("http://evil.test/api/tags", {}),                      # foreign origin
        ("https://fix.test/api/tags", {}),                      # scheme not declared
    ],
)
def test_opener_open_guard_denies(ledger_env, monkeypatch, url, kwargs):
    monkeypatch.setenv(cheap_llm.OBSERVE_HTTP_ALLOW_ENV, "http://fix.test")
    orig = Mock()
    monkeypatch.setattr(cheap_llm, "_opener_open_orig", orig)
    with pytest.raises(DispatchDeniedError):
        cheap_llm._opener_open_guard(Mock(), url, **kwargs)
    orig.assert_not_called()
    assert _events(ledger_env)[-1]["decision"] == "denied"


@allure.title("opener guard denies a POST Request object")
def test_opener_open_guard_denies_post_request(ledger_env, monkeypatch):
    monkeypatch.setenv(cheap_llm.OBSERVE_HTTP_ALLOW_ENV, "http://fix.test")
    request = urllib.request.Request("http://fix.test/api/tags", data=b"{}")
    with pytest.raises(DispatchDeniedError):
        cheap_llm._opener_open_guard(Mock(), request)


# ---------------------------------------------------------------------------
# install_observation / child exit / observed entrypoints
# ---------------------------------------------------------------------------


@allure.title("install_observation is idempotent")
def test_install_observation_already_installed(ledger_env, monkeypatch):
    monkeypatch.setattr(cheap_llm, "_observe_installed", True)
    popen = subprocess.Popen.__init__
    cheap_llm.install_observation()
    assert subprocess.Popen.__init__ is popen


@allure.title("install_observation fails closed when the ledger is missing")
def test_install_observation_missing_ledger(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(cheap_llm.OBSERVE_LEDGER_ENV, str(tmp_path / "gone.jsonl"))
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    with pytest.raises(SystemExit) as exc:
        cheap_llm.install_observation()
    assert exc.value.code == 3
    assert "refusing unobserved execution" in capsys.readouterr().err


@allure.title("install_observation arms every native and http guard")
def test_install_observation_arms_guards(installed):
    assert subprocess.Popen.__init__ is cheap_llm._native_launch_guard
    assert os.system is cheap_llm._os_system_guard
    assert urllib.request.OpenerDirector.open is cheap_llm._opener_open_guard
    assert cheap_llm._observe_installed is True
    rows = _events(installed)
    assert rows[0]["kind"] == "child_start"
    assert rows[0]["boundary"] == "interpreter_start_pre_product_import"
    cheap_llm._observed_child_exit()
    assert _events(installed)[-1]["kind"] == "child_exit"


@allure.title("installed run denies Popen, os.system and urlopen before the boundary")
def test_installed_run_denies_unadmitted_boundaries(installed):
    with pytest.raises(DispatchDeniedError):
        subprocess.Popen(["/bin/echo", "hi"])
    with pytest.raises(DispatchDeniedError):
        os.system("echo hi")
    if hasattr(os, "posix_spawn"):
        with pytest.raises(DispatchDeniedError):
            os.posix_spawn("/bin/echo", ["/bin/echo"], dict(os.environ))
    with pytest.raises(DispatchDeniedError):
        urllib.request.urlopen("http://fix.test/api/tags")


@allure.title("installed run admits a spawn matching the parent admission")
def test_installed_run_admits_registered_spawn(installed, tmp_path):
    env = dict(os.environ)
    argv = [sys.executable, "-c", "import sys;sys.exit(0)"]
    cheap_llm.admit_trusted_child(
        admission_id="adm-spawn",
        argv=argv,
        env=env,
        pass_fds=(),
        cwd=str(tmp_path),
        runner_sha256="r",
        source_sha256="s",
        source_bytes=1,
        script_path="x.py",
        authority="manifest:x.py",
        fd_device=0,
        fd_inode=0,
    )
    proc = subprocess.Popen(argv, env=env, cwd=str(tmp_path))
    assert proc.wait() == 0
    assert _events(installed)[-1]["decision"] == "admitted"


@allure.title("run_observed_module bootstraps then runs the module argv")
def test_run_observed_module(ledger_env, monkeypatch, tmp_path):
    installed_mock = Mock()
    monkeypatch.setattr(cheap_llm, "install_observation", installed_mock)
    monkeypatch.setattr(sys, "argv", ["entry"])
    with pytest.raises(SystemExit):
        cheap_llm.run_observed_module()
    marker = tmp_path / "ran.txt"
    (tmp_path / "obs_mod.py").write_text(
        "import sys,pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text('ran:' + sys.argv[0], encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["entry", "obs_mod", str(marker)])
    cheap_llm.run_observed_module()
    assert marker.read_text(encoding="utf-8") == "ran:obs_mod"
    installed_mock.assert_called_with()


@allure.title("run_observed_exec bootstraps then execs the code payload")
def test_run_observed_exec(ledger_env, monkeypatch, tmp_path):
    installed_mock = Mock()
    monkeypatch.setattr(cheap_llm, "install_observation", installed_mock)
    monkeypatch.setattr(sys, "argv", ["entry"])
    with pytest.raises(SystemExit):
        cheap_llm.run_observed_exec()
    marker = tmp_path / "ran.txt"
    monkeypatch.setattr(
        sys, "argv",
        ["entry", "import sys,pathlib;pathlib.Path(sys.argv[1]).write_text('ran')",
         str(marker)],
    )
    cheap_llm.run_observed_exec()
    assert marker.read_text(encoding="utf-8") == "ran"
    installed_mock.assert_called_with()


@allure.title("observed entries exist for hook launchers")
def test_observed_entry_constants():
    assert "run_observed_module" in cheap_llm.OBSERVED_MODULE_ENTRY
    assert "run_observed_exec" in cheap_llm.OBSERVED_EXEC_ENTRY


@allure.title("install_observation skips posix_spawn guards when the platform lacks them")
def test_install_observation_without_posix_spawn(monkeypatch, tmp_path):
    monkeypatch.delattr(os, "posix_spawn", raising=False)
    monkeypatch.delattr(os, "posix_spawnp", raising=False)
    ledger = tmp_path / "observed.jsonl"
    ledger.write_text("", encoding="utf-8")
    monkeypatch.setenv(cheap_llm.OBSERVE_LEDGER_ENV, str(ledger))
    monkeypatch.setattr(cheap_llm, "_observe_installed", False)
    monkeypatch.setattr(cheap_llm, "_child_admissions", {})
    saved = (
        subprocess.Popen.__init__,
        os.system,
        urllib.request.OpenerDirector.open,
    )
    try:
        cheap_llm.install_observation()
        assert subprocess.Popen.__init__ is cheap_llm._native_launch_guard
        assert os.system is cheap_llm._os_system_guard
        assert urllib.request.OpenerDirector.open is cheap_llm._opener_open_guard
        assert not hasattr(os, "posix_spawn")
        assert not hasattr(os, "posix_spawnp")
        assert cheap_llm._posix_spawn_orig is None
        assert cheap_llm._posix_spawnp_orig is None
    finally:
        (
            subprocess.Popen.__init__,
            os.system,
            urllib.request.OpenerDirector.open,
        ) = saved
        cheap_llm._observe_installed = False
        cheap_llm._popen_init_orig = None
        cheap_llm._opener_open_orig = None
        cheap_llm._os_system_orig = None
        cheap_llm._observe_attempt_ctx.set("")


@allure.title("health probe reports transport failures as unreachable")
def test_probe_health_transport_errors(monkeypatch):
    from greedy_token.settings import CheapLlmSettings

    settings = CheapLlmSettings(
        provider="ollama",
        url="http://fix.test:11434",
        model="m",
        source="default",
    )

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        Mock(side_effect=urllib.error.URLError("no route to host")),
    )
    probe = cheap_llm._probe_health(settings, timeout=0.01)
    assert probe.reachable is False
    assert probe.model_present is None
    assert "no route to host" in probe.reason

    monkeypatch.setattr(urllib.request, "urlopen", Mock(side_effect=OSError()))
    probe = cheap_llm._probe_health(settings, timeout=0.01)
    assert probe.reachable is False
    assert probe.reason == "unreachable"


@allure.title("status line falls back to 'start runtime' for a reasonless outage")
def test_cheap_llm_status_line_reasonless_outage(monkeypatch):
    from greedy_token.settings import CheapLlmSettings

    settings = CheapLlmSettings(
        provider="ollama",
        url="http://fix.test:11434",
        model="m",
        source="default",
    )
    monkeypatch.setattr(
        cheap_llm,
        "probe_cheap_llm",
        lambda *a, **k: cheap_llm.CheapLlmProbe(
            reachable=False, model_present=None, reason=""
        ),
    )
    line = cheap_llm.cheap_llm_status_line(settings)
    assert "Cheap LLM: unavailable (ollama, http://fix.test:11434)" in line
    assert "start runtime" in line
    assert "use expensive LLM (Cursor)" in line
