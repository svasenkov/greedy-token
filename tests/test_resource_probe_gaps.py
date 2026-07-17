"""Platform-independent tests for resource_probe (fail_under=100 on any OS)."""

from __future__ import annotations

import json
import subprocess
import sys
import types
import urllib.error
from pathlib import Path

import allure
import pytest

from greedy_token import resource_probe as rp
from greedy_token.resource_probe import (
    BenchmarkResult,
    DoctorReport,
    HardwareProfile,
    InstalledModel,
)

pytestmark = [
    allure.epic("Resource probe"),
    allure.parent_suite("Resource probe"),
    allure.feature("Hardware + Ollama doctor"),
    allure.suite("Resource probe gaps"),
]


@allure.title("_ram_gb: psutil path")
def test_ram_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("psutil")
    fake.virtual_memory = lambda: types.SimpleNamespace(total=16 * 1024**3, available=8 * 1024**3)
    monkeypatch.setitem(sys.modules, "psutil", fake)
    total, avail = rp._ram_gb()
    assert round(total) == 16 and round(avail) == 8


@allure.title("_ram_gb: Darwin sysctl, sysctl error, Linux meminfo, default")
def test_ram_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)  # force ImportError

    # Darwin sysctl success
    monkeypatch.setattr(rp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(rp.subprocess, "check_output", lambda *a, **k: str(8 * 1024**3))
    total, avail = rp._ram_gb()
    assert round(total) == 8 and round(avail) == 4

    # Darwin sysctl raises → default fallback
    def boom(*a, **k):
        raise OSError("no sysctl")

    monkeypatch.setattr(rp.subprocess, "check_output", boom)
    assert rp._ram_gb() == (8.0, 4.0)

    # Linux /proc/meminfo
    monkeypatch.setattr(rp.platform, "system", lambda: "Linux")
    real_is_file = Path.is_file
    real_read = Path.read_text

    def fake_is_file(self):
        if str(self) == "/proc/meminfo":
            return True
        return real_is_file(self)

    def fake_read(self, *a, **k):
        if str(self) == "/proc/meminfo":
            return "MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n"
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    monkeypatch.setattr(Path, "read_text", fake_read)
    total_l, avail_l = rp._ram_gb()
    assert total_l > 0 and avail_l > 0

    # meminfo with an irrelevant line and no MemTotal → total_kb 0 → default fallback
    def fake_read_no_total(self, *a, **k):
        if str(self) == "/proc/meminfo":
            return "SwapTotal:  1000 kB\nMemAvailable:  500 kB\n"
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", fake_read_no_total)
    assert rp._ram_gb() == (8.0, 4.0)


@allure.title("_gpu_info: Darwin Apple, Darwin non-apple, Darwin error, nvidia, nvidia error")
def test_gpu_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)

    # Darwin with Apple chipset
    monkeypatch.setattr(rp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(rp, "_ram_gb", lambda: (16.0, 8.0))
    monkeypatch.setattr(
        rp.subprocess, "check_output",
        lambda *a, **k: "Displays:\n  Chipset Model: Apple M2 Pro\n",
    )
    vram, name = rp._gpu_info()
    assert name == "Apple M2 Pro" and vram > 0

    # Darwin non-apple chipset → vram 0
    monkeypatch.setattr(
        rp.subprocess, "check_output",
        lambda *a, **k: "Displays:\n  Chipset Model: Radeon\n",
    )
    vram2, _ = rp._gpu_info()
    assert vram2 == 0.0

    # Darwin output without a Chipset line (loop finishes without break), M3 token present
    monkeypatch.setattr(rp.subprocess, "check_output", lambda *a, **k: "Displays:\n  Apple M3 stuff\n")
    vram_nc, name_nc = rp._gpu_info()
    assert name_nc == "Apple GPU" and vram_nc > 0

    # Darwin subprocess error
    def boom(*a, **k):
        raise subprocess.SubprocessError("x")

    monkeypatch.setattr(rp.subprocess, "check_output", boom)
    assert rp._gpu_info() == (0.0, "unknown")

    # Linux nvidia-smi success
    monkeypatch.setattr(rp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(rp.subprocess, "check_output", lambda *a, **k: "NVIDIA RTX 4090, 24576\n")
    vram3, name3 = rp._gpu_info()
    assert name3 == "NVIDIA RTX 4090" and round(vram3) == 24

    # nvidia error → cpu_only
    monkeypatch.setattr(rp.subprocess, "check_output", boom)
    assert rp._gpu_info() == (0.0, "cpu_only")


@allure.title("detect_hardware maps vram to all tiers")
def test_detect_hardware_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rp, "_ram_gb", lambda: (16.0, 8.0))
    monkeypatch.setattr(rp, "_cpu_cores", lambda: 8)
    for vram, tier in [(0.0, "cpu_only"), (4.0, "low_vram"), (12.0, "mid_vram"), (24.0, "high_vram")]:
        monkeypatch.setattr(rp, "_gpu_info", lambda v=vram: (v, "gpu"))
        assert rp.detect_hardware().tier == tier


@allure.title("_is_avoided matches base and substring")
def test_is_avoided() -> None:
    assert rp._is_avoided("openchat:7b", ["openchat"]) is True
    assert rp._is_avoided("qwen2.5:7b", ["openchat"]) is False


@allure.title("_is_deprecated returns string reason when info is not a dict")
def test_is_deprecated_string_info() -> None:
    dep, reason = rp._is_deprecated("legacy:7b", {"deprecated": {"legacy": "just a string reason"}})
    assert dep is True and reason == "just a string reason"


@allure.title("catalog_path resource fallback + missing file")
def test_catalog_path_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    # resources ref is a file → returns that path
    class FakeRef:
        def is_file(self):
            return True

        def __str__(self):
            return "/fake/model_catalog.yaml"

    monkeypatch.setattr(rp.resources, "files", lambda *a, **k: types.SimpleNamespace(joinpath=lambda p: FakeRef()))
    assert rp.catalog_path().name == "model_catalog.yaml"

    # resources ref exists but is_file() False → falls to default path
    class MissingRef:
        def is_file(self):
            return False

    monkeypatch.setattr(rp.resources, "files", lambda *a, **k: types.SimpleNamespace(joinpath=lambda p: MissingRef()))
    assert rp.catalog_path().name == "model_catalog.yaml"

    # resources raises → default path
    monkeypatch.setattr(rp.resources, "files", lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError()))
    assert rp.catalog_path().name == "model_catalog.yaml"

    monkeypatch.setattr(Path, "is_file", real_is_file)
    # load_model_catalog with missing file → {}
    monkeypatch.setattr(rp, "catalog_path", lambda: Path("/no/such/catalog.yaml"))
    assert rp.load_model_catalog() == {}


@allure.title("fetch_ollama_models: parses models and returns [] on error")
def test_fetch_ollama_models(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    from contextlib import contextmanager

    @contextmanager
    def fake_open(*a, **k):
        yield io.BytesIO(json.dumps({"models": [{"name": "qwen2.5:7b", "size": 100}, {"name": ""}]}).encode())

    monkeypatch.setattr(rp.urllib.request, "urlopen", lambda *a, **k: fake_open())
    models = rp.fetch_ollama_models("http://o:11434")
    assert len(models) == 1 and models[0].name == "qwen2.5:7b"

    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(rp.urllib.request, "urlopen", boom)
    assert rp.fetch_ollama_models("http://o:11434") == []


@allure.title("recommend_avoid returns list for tier")
def test_recommend_avoid() -> None:
    hw = HardwareProfile("cpu_only", 8, 4, 0, 4, "gpu", "Linux")
    assert isinstance(rp.recommend_avoid(hw), list)


@allure.title("probe cache load/save round-trip and error handling")
def test_probe_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "probe-cache.json"
    monkeypatch.setattr(rp, "PROBE_CACHE_PATH", cache)
    assert rp._load_probe_cache() == {}
    rp._save_probe_cache({"k": {"ts": 1}})
    assert rp._load_probe_cache() == {"k": {"ts": 1}}
    cache.write_text("{not json", encoding="utf-8")
    assert rp._load_probe_cache() == {}


@allure.title("run_micro_benchmark: cache hit, unavailable, success, chat error")
def test_run_micro_benchmark(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "probe-cache.json"
    monkeypatch.setattr(rp, "PROBE_CACHE_PATH", cache)

    # cache hit
    import time

    cache.write_text(
        json.dumps({"bench:m": {"ts": time.time(), "latency_ms": 5, "eval_tokens": 3, "ok": True, "error": ""}}),
        encoding="utf-8",
    )
    res = rp.run_micro_benchmark("m", quick=True, use_cache=True)
    assert res.ok is True and res.latency_ms == 5

    # cache present but expired (ts=0) with quick+use_cache → proceeds past cache block
    cache.write_text(json.dumps({"bench:m": {"ts": 0, "ok": True}}), encoding="utf-8")
    monkeypatch.setattr(rp, "cheap_llm_available", lambda *a, **k: False)
    expired = rp.run_micro_benchmark("m", quick=True, use_cache=True)
    assert expired.ok is False

    # unavailable
    cache.unlink()
    monkeypatch.setattr(rp, "cheap_llm_available", lambda *a, **k: False)
    res2 = rp.run_micro_benchmark("m", quick=False, use_cache=False)
    assert res2.ok is False and "unavailable" in res2.error

    # success + caches
    monkeypatch.setattr(rp, "cheap_llm_available", lambda *a, **k: True)
    monkeypatch.setattr(rp, "cheap_llm_chat", lambda *a, **k: ("ok", 12))
    res3 = rp.run_micro_benchmark("m", quick=False, use_cache=True)
    assert res3.ok is True and res3.eval_tokens == 12
    assert cache.is_file()

    # chat raises
    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(rp, "cheap_llm_chat", boom)
    res4 = rp.run_micro_benchmark("m", quick=False, use_cache=False)
    assert res4.ok is False and "boom" in res4.error


@allure.title("paid_economy_recommendations: filters and sorts")
def test_paid_economy_recommendations(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = {
        "paid_models": {
            "cheap-one": {"cost_per_1m_usd": 1, "quality_tier": "budget", "note": "cheap", "profiles": ["classify"]},
            "mid-one": {"cost_per_1m_usd": 2, "quality_tier": "mid", "profiles": ["classify"]},
            "other-profile": {"cost_per_1m_usd": 0.1, "profiles": ["generate"]},
            "not-a-dict": "skip",
        }
    }
    monkeypatch.setattr(rp, "load_model_catalog", lambda: catalog)
    recs = rp.paid_economy_recommendations(profile="classify")
    assert recs and "cheap-one" in recs[0]
    # enabled filter
    filtered = rp.paid_economy_recommendations(["mid-one"], profile="classify")
    assert all("cheap-one" not in r for r in filtered)


def _report(**kw) -> DoctorReport:
    base = dict(
        hardware=HardwareProfile("mid_vram", 16, 8, 12, 8, "gpu", "Linux"),
        ollama_available=True, ollama_url="http://o", installed=[], configured_model="qwen2.5:7b",
        recommended=["qwen2.5-coder:7b"], deprecated_installed=[], avoid_installed=[],
    )
    base.update(kw)
    return DoctorReport(**base)


@allure.title("run_doctor: warnings, benchmark, paid import fallback")
def test_run_doctor_branches(monkeypatch: pytest.MonkeyPatch, minimal_workspace: Path) -> None:
    monkeypatch.setattr(rp, "detect_hardware", lambda: HardwareProfile("mid_vram", 16, 8, 12, 8, "gpu", "Linux"))
    monkeypatch.setattr(rp, "load_model_catalog", lambda: {"tiers": {"mid_vram": {"recommend": ["qwen2.5-coder:7b"], "avoid": ["openchat"]}}, "deprecated": {"openchat": {"reason": "old"}}})
    monkeypatch.setattr(rp, "cheap_llm_available", lambda *a, **k: True)
    installed = [
        InstalledModel("openchat:7b", 100, deprecated=True, deprecated_reason="old"),
        InstalledModel("openchat:latest", 50),
    ]
    monkeypatch.setattr(rp, "fetch_ollama_models", lambda *a, **k: installed)
    monkeypatch.setattr(rp, "get_cheap_llm_settings", lambda root=None: types.SimpleNamespace(url="http://o", model="openchat:7b"))
    monkeypatch.setattr(rp, "run_micro_benchmark", lambda *a, **k: BenchmarkResult("m", 10, 5, True))

    report = rp.run_doctor(root=minimal_workspace, benchmark=True, include_paid=True)
    assert any("Deprecated" in w for w in report.warnings)
    assert any("Suboptimal" in w for w in report.warnings)
    assert any("deprecated" in w for w in report.warnings)
    assert report.benchmark is not None

    # ollama unavailable path + paid import fallback
    monkeypatch.setattr(rp, "cheap_llm_available", lambda *a, **k: False)

    def boom_registry(root):
        raise ImportError("no model_select")

    monkeypatch.setattr("greedy_token.model_select.get_llm_registry", boom_registry)
    report2 = rp.run_doctor(root=None, include_paid=True)
    assert any("unavailable" in w for w in report2.warnings)


@allure.title("format_doctor_report covers installed, benchmark ok/fail, paid, warnings")
def test_format_doctor_report() -> None:
    ok = _report(
        installed=[InstalledModel("qwen2.5:7b", 2 * 1024**2), InstalledModel("openchat:7b", 1024**2, deprecated=True)],
        benchmark=BenchmarkResult("m", 10, 5, True),
        paid_recommendations=["paid-a"],
        warnings=["watch out"],
    )
    text = rp.format_doctor_report(ok, include_paid=True)
    assert "[deprecated]" in text and "Benchmark" in text and "paid-a" in text and "watch out" in text

    fail = _report(installed=[], benchmark=BenchmarkResult("m", 0, None, False, error="nope"))
    text2 = rp.format_doctor_report(fail)
    assert "installed: (none)" in text2 and "Benchmark failed" in text2


@allure.title("apply_doctor_config: no model, update existing, non-dict cfg/sections, init new")
def test_apply_doctor_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rp, "run_doctor", lambda **k: _report(recommended=[]))
    with pytest.raises(ValueError, match="No model recommendation"):
        rp.apply_doctor_config()

    monkeypatch.setattr(rp, "run_doctor", lambda **k: _report(recommended=["qwen2.5-coder:7b"]))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("cheap_llm:\n  model: old\n", encoding="utf-8")
    monkeypatch.setattr("greedy_token.settings.user_config_path", lambda: cfg)
    out = rp.apply_doctor_config()
    assert out == cfg
    assert "qwen2.5-coder:7b" in cfg.read_text(encoding="utf-8")

    # existing file whose yaml is not a dict → cfg reset to {}
    cfg.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert rp.apply_doctor_config() == cfg

    # existing file where sections are non-dict scalars → skipped without error
    cfg.write_text("cheap_llm: scalar\nollama: scalar\n", encoding="utf-8")
    assert rp.apply_doctor_config() == cfg

    # force → delegates to init_user_config
    called: dict = {}

    def fake_init(**k):
        called["kw"] = k
        return cfg

    monkeypatch.setattr("greedy_token.settings.init_user_config", fake_init)
    out2 = rp.apply_doctor_config(force=True)
    assert out2 == cfg and called["kw"]["model"] == "qwen2.5-coder:7b"


@allure.title("local_health_line: unavailable, deprecated, ok, exception")
def test_local_health_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rp, "run_doctor", lambda **k: _report(ollama_available=False))
    assert "unavailable" in rp.local_health_line()

    monkeypatch.setattr(rp, "run_doctor", lambda **k: _report(deprecated_installed=["openchat:7b"]))
    assert "deprecated" in rp.local_health_line()

    monkeypatch.setattr(rp, "run_doctor", lambda **k: _report())
    assert "OK" in rp.local_health_line()

    def boom(**k):
        raise RuntimeError("x")

    monkeypatch.setattr(rp, "run_doctor", boom)
    assert "probe skipped" in rp.local_health_line()
