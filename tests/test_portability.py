"""Portability regressions — Windows branches and non-UTF-8 locales, proven
without a Windows host.

Two emulation techniques:

* ``sys.platform`` + ``sys.modules`` monkeypatching for the OS-lock and
  tool-lookup branches (``os.name`` is deliberately not patched — ``Path()``
  picks its concrete flavour from it and would explode mid-test);
* a ``sys.executable`` child under ``LC_ALL=C PYTHONUTF8=0
  PYTHONCOERCECLOCALE=0``, where the stdio/filesystem default encoding is
  ASCII — the POSIX stand-in for a Windows ANSI codepage.  Pairing each
  reader with a naive ``encoding=None`` probe of the same bytes demonstrates
  the non-equivalence empirically instead of asserting it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

import allure
from greedy_token import spend_ledger, tool_paths, usage
from greedy_token.model_select import ModelSpec
from greedy_token.spend_guard import reserve_metered_call
from greedy_token.subprocess_safe import (
    UnsafeCommandError,
    is_absolute_path,
    trusted_script_argv,
    trusted_tool_invocation,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

pytestmark = [
    allure.epic("Portability"),
    allure.parent_suite("Portability"),
    allure.feature("Cross-platform regressions"),
    allure.suite("Cross-platform regressions"),
]


def _ascii_locale_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "LC_ALL": "C",
            "PYTHONUTF8": "0",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONPATH": str(SRC_DIR),
            # Keep child stderr/stdout decodable when it echoes Cyrillic.
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _ascii_child(snippet: str, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", snippet, *(str(arg) for arg in args)],
        env=_ascii_locale_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _metered_spec() -> ModelSpec:
    return ModelSpec(  # type: ignore[arg-type]
        id="bulk",
        enabled=True,
        provider="openai_compat",
        url="https://x",
        model="m",
        profiles=("*",),
        locality="remote",
        billing="metered",
        cost_per_1m_usd=0.2,
    )


# --------------------------------------------------------------------------
# spend_lock: OS-level lock per platform, degrade only as a last resort
# --------------------------------------------------------------------------


@allure.title("POSIX branch: fcntl lock file sidecar is taken")
def test_spend_lock_posix_uses_fcntl_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX branch only")
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    with spend_ledger.spend_lock():
        spend_ledger.reserve_spend(
            reservation_id="posix1", model_id="m", est_usd=0.01
        )
    assert spend_ledger.spend_log_path().with_suffix(".lock").is_file()
    assert spend_ledger.ledger_spend_usd() == pytest.approx(0.01)


@allure.title("no fcntl: in-process lock still serializes, no sidecar created")
def test_spend_lock_degrades_without_fcntl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    # `sys.modules[name] = None` makes `import name` raise ImportError.
    monkeypatch.setitem(sys.modules, "fcntl", None)
    with spend_ledger.spend_lock():
        spend_ledger.reserve_spend(
            reservation_id="nofcntl", model_id="m", est_usd=0.02
        )
    assert spend_ledger.ledger_spend_usd() == pytest.approx(0.02)
    assert not spend_ledger.spend_log_path().with_suffix(".lock").exists()


@allure.title("Windows branch: msvcrt.locking wraps the critical section")
def test_spend_lock_windows_uses_msvcrt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    fake = types.ModuleType("msvcrt")
    fake.LK_LOCK = 1  # type: ignore[attr-defined]
    fake.LK_UNLCK = 0  # type: ignore[attr-defined]
    calls: list[tuple[int, int]] = []
    fake.locking = lambda fd, mode, nbytes: calls.append((mode, nbytes))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    with spend_ledger.spend_lock():
        spend_ledger.reserve_spend(
            reservation_id="win1", model_id="m", est_usd=0.03
        )
    assert calls == [(1, 1), (0, 1)]  # LK_LOCK then LK_UNLCK, one byte
    assert spend_ledger.spend_log_path().with_suffix(".lock").is_file()
    assert spend_ledger.ledger_spend_usd() == pytest.approx(0.03)


@allure.title("Windows without msvcrt: documented degrade still works")
def test_spend_lock_windows_degrades_without_msvcrt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    monkeypatch.setattr(sys, "platform", "win32")
    with spend_ledger.spend_lock():
        spend_ledger.reserve_spend(
            reservation_id="win-deg", model_id="m", est_usd=0.04
        )
    assert spend_ledger.ledger_spend_usd() == pytest.approx(0.04)


@allure.title("lock acquisition failure denies the metered call (fail closed)")
def test_reserve_denied_when_lock_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def _boom():
        raise OSError("lock timeout")
        yield

    monkeypatch.setattr("greedy_token.spend_guard.spend_lock", _boom)
    reservation = reserve_metered_call(_metered_spec())
    assert not reservation.allowed
    assert "lock" in reservation.reason.lower()


# --------------------------------------------------------------------------
# JSONL byte parity: LF-terminated records, CRLF-tolerant readers
# --------------------------------------------------------------------------


@allure.title("JSONL writers emit LF bytes on every platform (no CRLF mix)")
def test_jsonl_appends_are_lf_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spend_log = tmp_path / "spend.jsonl"
    usage_log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(spend_log))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(usage_log))
    spend_ledger.reserve_spend(reservation_id="lf", model_id="m", est_usd=0.01)
    usage.append_event(
        {"ts": datetime.now(UTC).isoformat(), "task": "кириллица"},
        path=usage_log,
    )
    for path in (spend_log, usage_log):
        data = path.read_bytes()
        assert b"\r" not in data, f"CRLF leaked into {path.name}"
        assert data.endswith(b"\n")


@allure.title("spend reader parses CRLF files and \\r inside JSON strings")
def test_spend_ledger_reads_crlf_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "spend.jsonl"
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(log))
    rows = [
        {"ts": "2026-01-01T00:00:00+00:00", "kind": "reserve", "id": "r\rid",
         "model_id": "m", "est_usd": 0.5},
        {"ts": "2026-01-01T00:00:01+00:00", "kind": "settle", "id": "r\rid",
         "cost_usd": 0.7},
    ]
    # \r inside a string is escaped by json.dumps; the CRLF here is the line
    # terminator a Windows/manual writer would produce.
    log.write_bytes(("\r\n".join(json.dumps(r) for r in rows) + "\r\n").encode())
    assert spend_ledger.ledger_spend_usd() == pytest.approx(0.7)


# --------------------------------------------------------------------------
# encoding=None is NOT equivalent to encoding="utf-8" on non-UTF-8 locales —
# refutation of the UTF-8-locale proofs in docs/mutation-equivalents.yaml
# --------------------------------------------------------------------------


@allure.title("usage_metered_spend_usd reads UTF-8 under an ASCII locale")
def test_usage_metered_spend_utf8_on_ascii_locale(tmp_path: Path) -> None:
    # 'Ё' is U+0401 → UTF-8 D0 81, and 0x81 is undefined in cp1252 — the
    # sharpest non-equivalence probe for both ASCII and ANSI defaults.
    log = tmp_path / "usage.jsonl"
    event = {
        "ts": "2026-01-01T00:00:00+00:00",
        "task": "Ёлки",
        "billing": {"tier": "metered", "cost_usd": 1.5},
    }
    log.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

    naive = _ascii_child(
        "import sys; from pathlib import Path; "
        "print(Path(sys.argv[1]).read_text().splitlines()[0][:2])",
        log,
    )
    assert naive.returncode != 0, "encoding=None must diverge on ASCII locale"

    real = _ascii_child(
        "import sys; from pathlib import Path; "
        "from greedy_token.spend_ledger import usage_metered_spend_usd; "
        "print(f'{usage_metered_spend_usd(log=Path(sys.argv[1])):.4f}')",
        log,
    )
    assert real.returncode == 0, real.stderr
    assert real.stdout.strip() == "1.5000"


@allure.title("spend ledger writes and reads UTF-8 under an ASCII locale")
def test_spend_ledger_utf8_on_ascii_locale(tmp_path: Path) -> None:
    # Covers both directions of the pinned encoding: _append_spend_record's
    # open(encoding="utf-8") and _iter_spend_records' read_text(encoding="utf-8")
    # — under encoding=None either side raises Unicode*Error on ASCII locale.
    log = tmp_path / "spend.jsonl"
    # argv must stay pure ASCII: under LC_ALL=C the child cannot decode
    # non-ASCII argv, so Cyrillic rides in as \uXXXX escapes.
    snippet = (
        "import os, sys; from pathlib import Path; "
        "from greedy_token import spend_ledger; "
        "os.environ['GREEDY_TOKEN_SPEND_LOG'] = sys.argv[1]; "
        "spend_ledger.reserve_spend("
        "reservation_id='r', model_id='\\u0401\\u043b\\u043a\\u0438', est_usd=1.0); "
        "print(f'{spend_ledger.ledger_spend_usd():.4f}')"
    )
    real = _ascii_child(snippet, log)
    assert real.returncode == 0, real.stderr
    assert real.stdout.strip() == "1.0000"


@allure.title("trust manifest reads UTF-8 under an ASCII locale")
def test_trust_manifest_utf8_on_ascii_locale(tmp_path: Path) -> None:
    snippet = (
        "import sys; from pathlib import Path; "
        "from greedy_token.trust import (TrustEntry, FileIdentity, "
        "_read_entries, _write_entries); "
        "root = Path(sys.argv[1]); "
        # argv must stay pure ASCII: 'проверка' rides in as \uXXXX escapes.
        "entry = TrustEntry(path='scripts/\\u043f\\u0440\\u043e\\u0432\\u0435\\u0440\\u043a\\u0430.py', "
        "sha256='a' * 64, "
        "script_type='python', approved_at='2026-01-01T00:00:00+00:00', "
        "approval_source='t', file_identity=FileIdentity(device=1, inode=1)); "
        "_write_entries(root, (entry,)); "
        "assert _read_entries(root)[0].path == 'scripts/\\u043f\\u0440\\u043e\\u0432\\u0435\\u0440\\u043a\\u0430.py'; "
        "print('ok')"
    )
    proc = _ascii_child(snippet, tmp_path / "ws")
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


@allure.title("pipelines.yaml is not ASCII — the pure-ASCII claim is refuted")
def test_pipelines_yaml_not_ascii() -> None:
    data = (SRC_DIR / "greedy_token" / "config" / "pipelines.yaml").read_bytes()
    assert any(b > 127 for b in data), (
        "registry entry for _load_pipelines_config assumes pure ASCII"
    )


@allure.title("_load_pipelines_config parses UTF-8 YAML under an ASCII locale")
def test_load_pipelines_config_utf8_on_ascii_locale() -> None:
    target = SRC_DIR / "greedy_token" / "config" / "pipelines.yaml"
    naive = _ascii_child(
        "import sys; print(open(sys.argv[1]).read()[:4])",
        target,
    )
    assert naive.returncode != 0, "encoding=None must diverge on ASCII locale"

    real = _ascii_child(
        "from greedy_token.pipeline import _load_pipelines_config; "
        "cfg = _load_pipelines_config(); "
        "assert cfg, 'pipelines.yaml must parse'; "
        "print('ok', len(cfg))"
    )
    assert real.returncode == 0, real.stderr
    assert "ok" in real.stdout


@allure.title("_estimate_step_tokens counts UTF-8 skill text under ASCII locale")
def test_estimate_step_tokens_utf8_on_ascii_locale(tmp_path: Path) -> None:
    from greedy_token.tokens import count_tokens

    skill = tmp_path / "skill.md"
    body = "Проверь структуру скилла: название, описание, примеры. " * 4
    skill.write_text(body, encoding="utf-8")
    expected = count_tokens(body).tokens + count_tokens("out").tokens

    proc = _ascii_child(
        "import sys; from pathlib import Path; "
        "from greedy_token.pipeline import PipelineStep, _estimate_step_tokens; "
        "step = PipelineStep(step_id='audit-skill', tier='ollama', label='x', "
        "args=sys.argv[2]); "
        "print(_estimate_step_tokens(step, 'out', Path(sys.argv[1])))",
        tmp_path,
        skill.name,
    )
    assert proc.returncode == 0, proc.stderr
    got = int(proc.stdout.strip())
    assert got == expected

    naive = _ascii_child(
        "import sys; from pathlib import Path; "
        "from greedy_token.tokens import count_tokens; "
        "print(count_tokens(Path(sys.argv[1]).read_text("
        "errors='replace')).tokens)",
        skill,
    )
    assert naive.returncode == 0, naive.stderr
    assert int(naive.stdout.strip()) + count_tokens("out").tokens != expected, (
        "encoding=None must produce a different token count"
    )


# --------------------------------------------------------------------------
# Windows path forms must fail closed on every host OS
# --------------------------------------------------------------------------


@allure.title("drive-relative paths are not absolute but are never trusted")
@pytest.mark.parametrize(
    "value",
    ["C:relative", "c:x", "D:x/y", "C:"],
)
def test_drive_relative_is_not_absolute_yet_rejected(value: str, tmp_path: Path) -> None:
    # ntpath.isabs("C:x") is False — drive-relative resolves against the
    # per-drive cwd on Windows.  It must still never reach a script arg.
    assert not is_absolute_path(value)
    root = tmp_path / "ws"
    script = root / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    with pytest.raises(UnsafeCommandError, match="drive"):
        trusted_script_argv(
            (sys.executable, "scripts/check.py", f"--target={value}"),
            cwd=root,
            root=root,
            registered_script_paths=("scripts/check.py",),
        )


@allure.title("Windows absolute and UNC script args are refused on any host")
@pytest.mark.parametrize(
    "value",
    [r"C:\outside\x.txt", r"\\server\share\f.txt", r"C:/abs/x.txt"],
)
def test_script_args_reject_windows_absolute(value: str, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    script = root / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    for arg in (value, f"--target={value}"):
        with pytest.raises(UnsafeCommandError):
            trusted_script_argv(
                (sys.executable, "scripts/check.py", arg),
                cwd=root,
                root=root,
                registered_script_paths=("scripts/check.py",),
            )


@allure.title("script operand rejects drive-relative and UNC forms")
@pytest.mark.parametrize(
    "script_arg",
    ["C:evil.py", r"\\server\share\evil.py", r"C:\evil.py"],
)
def test_script_path_rejects_windows_forms(script_arg: str, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(UnsafeCommandError):
        trusted_script_argv(
            (sys.executable, script_arg),
            cwd=root,
            root=root,
        )


@allure.title("tool path operands reject Windows absolute/drive-relative")
@pytest.mark.parametrize(
    "operand",
    [r"C:\outside", r"\\server\share", "C:relative"],
)
def test_tool_invocation_rejects_windows_paths(operand: str, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    argv = ("rg", "-n", "needle", "--max-count", "50", "--", "needle", operand)
    with pytest.raises(UnsafeCommandError):
        trusted_tool_invocation(argv, cwd=root, root=root, tool="rg")


@allure.title("PATH scan finds rg.exe/jq.exe under win32 PATHEXT")
def test_tool_lookup_finds_exe_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    exe = tool_dir / "rg.exe"
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.delenv("GREEDY_TOKEN_RG", raising=False)
    monkeypatch.setenv("PATH", str(tool_dir))
    monkeypatch.setenv("PATHEXT", ".EXE;.BAT;.CMD")
    monkeypatch.setattr("greedy_token.tool_paths.shutil.which", lambda *a, **k: None)
    monkeypatch.setattr(sys, "platform", "win32")
    assert tool_paths.resolve_rg() == exe.resolve()


@allure.title("PATH scan emits PATHEXT candidate names on win32 only")
def test_tool_candidates_pathext_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_JQ", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    monkeypatch.setattr("greedy_token.tool_paths.shutil.which", lambda *a, **k: None)

    monkeypatch.setattr(sys, "platform", "win32")
    win_names = {
        p.name
        for p in tool_paths._tool_candidates("jq", override_var="GREEDY_TOKEN_JQ")
    }
    assert "jq.exe" in win_names

    monkeypatch.setattr(sys, "platform", "darwin")
    posix_names = {
        p.name
        for p in tool_paths._tool_candidates("jq", override_var="GREEDY_TOKEN_JQ")
    }
    assert "jq.exe" not in posix_names
