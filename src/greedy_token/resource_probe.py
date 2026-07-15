"""Hardware + Ollama inventory probe and model recommendations."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from greedy_token.cheap_llm import cheap_llm_available, cheap_llm_chat
from greedy_token.settings import get_cheap_llm_settings

HardwareTier = str  # cpu_only | low_vram | mid_vram | high_vram

PROBE_CACHE_PATH = Path.home() / ".greedy-token" / "probe-cache.json"
PROBE_CACHE_TTL_S = 86400


@dataclass(frozen=True)
class HardwareProfile:
    tier: HardwareTier
    ram_gb_total: float
    ram_gb_available: float
    vram_gb: float
    cpu_cores: int
    gpu_name: str
    platform: str


@dataclass(frozen=True)
class InstalledModel:
    name: str
    size_bytes: int
    deprecated: bool = False
    deprecated_reason: str = ""


@dataclass(frozen=True)
class BenchmarkResult:
    model: str
    latency_ms: int
    eval_tokens: int | None
    ok: bool
    error: str = ""


@dataclass
class DoctorReport:
    hardware: HardwareProfile
    ollama_available: bool
    ollama_url: str
    installed: list[InstalledModel]
    configured_model: str
    recommended: list[str]
    deprecated_installed: list[str]
    avoid_installed: list[str]
    benchmark: BenchmarkResult | None = None
    paid_recommendations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def catalog_path() -> Path:
    for candidate in (
        Path(__file__).resolve().parent / "config" / "model_catalog.yaml",
        Path(__file__).resolve().parent.parent / "greedy_token" / "config" / "model_catalog.yaml",
    ):
        if candidate.is_file():
            return candidate
    try:
        ref = resources.files("greedy_token").joinpath("config/model_catalog.yaml")
        if ref.is_file():
            return Path(str(ref))
    except (TypeError, FileNotFoundError, ModuleNotFoundError):
        pass
    return Path(__file__).resolve().parent / "config" / "model_catalog.yaml"


def load_model_catalog() -> dict[str, Any]:
    path = catalog_path()
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _ram_gb() -> tuple[float, float]:
    try:
        import psutil  # optional — not a hard dependency

        vm = psutil.virtual_memory()
        return vm.total / (1024**3), vm.available / (1024**3)
    except ImportError:
        pass

    system = platform.system()
    if system == "Darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=2).strip()
            total = int(out) / (1024**3)
            return total, total * 0.5
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    if system == "Linux" and Path("/proc/meminfo").is_file():
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        total_kb = avail_kb = 0
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
        if total_kb:
            return total_kb / (1024**2), avail_kb / (1024**2)
    return 8.0, 4.0


def _cpu_cores() -> int:
    return os.cpu_count() or 4


def _gpu_info() -> tuple[float, str]:
    system = platform.system()
    if system == "Darwin":
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            )
            name = "Apple GPU"
            for line in out.splitlines():
                if "Chipset Model:" in line:
                    name = line.split(":", 1)[1].strip()
                    break
            # Unified memory — estimate VRAM as fraction of RAM
            total_ram, _ = _ram_gb()
            vram = min(total_ram * 0.75, 24.0) if "Apple" in name or "M1" in out or "M2" in out or "M3" in out else 0.0
            return vram, name
        except (subprocess.SubprocessError, OSError):
            return 0.0, "unknown"
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        line = out.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        name = parts[0] if parts else "NVIDIA GPU"
        vram_mb = float(parts[1]) if len(parts) > 1 else 0.0
        return vram_mb / 1024, name
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return 0.0, "cpu_only"


def detect_hardware() -> HardwareProfile:
    ram_total, ram_avail = _ram_gb()
    vram, gpu_name = _gpu_info()
    cores = _cpu_cores()

    if vram <= 0.5:
        tier: HardwareTier = "cpu_only"
    elif vram < 8:
        tier = "low_vram"
    elif vram < 16:
        tier = "mid_vram"
    else:
        tier = "high_vram"

    return HardwareProfile(
        tier=tier,
        ram_gb_total=round(ram_total, 1),
        ram_gb_available=round(ram_avail, 1),
        vram_gb=round(vram, 1),
        cpu_cores=cores,
        gpu_name=gpu_name,
        platform=platform.system(),
    )


def _model_base(name: str) -> str:
    return name.split(":")[0].lower()


def _is_deprecated(name: str, catalog: dict[str, Any]) -> tuple[bool, str]:
    deprecated = catalog.get("deprecated") or {}
    lower = name.lower()
    base = _model_base(name)
    for key, info in deprecated.items():
        if key.lower() in lower or key.lower() == base:
            if isinstance(info, dict):
                return True, str(info.get("reason", "deprecated"))
            return True, str(info)
    return False, ""


def _is_avoided(name: str, avoid_list: list[str]) -> bool:
    lower = name.lower()
    base = _model_base(name)
    for item in avoid_list:
        if item.lower() in lower or item.lower() == base:
            return True
    return False


def fetch_ollama_models(url: str | None = None, *, timeout: float = 3.0) -> list[InstalledModel]:
    settings = get_cheap_llm_settings()
    base_url = (url or settings.url).rstrip("/")
    catalog = load_model_catalog()
    try:
        req = urllib.request.Request(f"{base_url}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    models: list[InstalledModel] = []
    for entry in data.get("models") or []:
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        size = int(entry.get("size") or 0)
        dep, reason = _is_deprecated(name, catalog)
        models.append(InstalledModel(name=name, size_bytes=size, deprecated=dep, deprecated_reason=reason))
    return models


def recommend_models(hw: HardwareProfile, catalog: dict[str, Any] | None = None) -> list[str]:
    catalog = catalog or load_model_catalog()
    tiers = catalog.get("tiers") or {}
    tier_cfg = tiers.get(hw.tier) or tiers.get("cpu_only") or {}
    rec = tier_cfg.get("recommend") or ["qwen2.5-coder:7b-instruct-q4_K_M"]
    return [str(x) for x in rec]


def recommend_avoid(hw: HardwareProfile, catalog: dict[str, Any] | None = None) -> list[str]:
    catalog = catalog or load_model_catalog()
    tiers = catalog.get("tiers") or {}
    tier_cfg = tiers.get(hw.tier) or {}
    return [str(x) for x in (tier_cfg.get("avoid") or [])]


def _load_probe_cache() -> dict[str, Any]:
    if not PROBE_CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(PROBE_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_probe_cache(payload: dict[str, Any]) -> None:
    PROBE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBE_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_micro_benchmark(
    model: str,
    *,
    quick: bool = True,
    use_cache: bool = True,
) -> BenchmarkResult:
    cache_key = f"bench:{model}"
    if use_cache and quick:
        cached = _load_probe_cache()
        entry = cached.get(cache_key)
        if entry and time.time() - entry.get("ts", 0) < PROBE_CACHE_TTL_S:
            return BenchmarkResult(
                model=model,
                latency_ms=int(entry.get("latency_ms", 0)),
                eval_tokens=entry.get("eval_tokens"),
                ok=bool(entry.get("ok")),
                error=str(entry.get("error", "")),
            )

    catalog = load_model_catalog()
    bench = catalog.get("benchmark") or {}
    system = str(bench.get("system", "You are a code classifier."))
    user = str(bench.get("user", "def foo(): pass"))

    settings = get_cheap_llm_settings()
    from greedy_token.settings import CheapLlmSettings

    probe_settings = CheapLlmSettings(
        provider=settings.provider,
        url=settings.url,
        model=model,
        source="probe",
        api_key=settings.api_key,
    )

    if not cheap_llm_available(probe_settings, timeout=2.0):
        return BenchmarkResult(model=model, latency_ms=0, eval_tokens=None, ok=False, error="ollama unavailable")

    t0 = time.perf_counter()
    try:
        _, eval_tokens = cheap_llm_chat(probe_settings, system=system, user=user, timeout=60.0)
        latency = int((time.perf_counter() - t0) * 1000)
        result = BenchmarkResult(model=model, latency_ms=latency, eval_tokens=eval_tokens, ok=True)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        result = BenchmarkResult(model=model, latency_ms=0, eval_tokens=None, ok=False, error=str(exc))

    if use_cache:
        cached = _load_probe_cache()
        cached[cache_key] = {
            "ts": time.time(),
            "latency_ms": result.latency_ms,
            "eval_tokens": result.eval_tokens,
            "ok": result.ok,
            "error": result.error,
        }
        _save_probe_cache(cached)
    return result


def paid_economy_recommendations(
    enabled_models: list[str] | None = None,
    *,
    profile: str = "classify",
) -> list[str]:
    catalog = load_model_catalog()
    paid = catalog.get("paid_models") or {}
    lines: list[str] = []
    candidates: list[tuple[float, str, str]] = []

    for model_id, info in paid.items():
        if not isinstance(info, dict):
            continue
        if enabled_models is not None and model_id not in enabled_models:
            continue
        profiles = info.get("profiles") or []
        if profile and profiles and profile not in profiles:
            continue
        cost = float(info.get("cost_per_1m_usd") or 0)
        quality = str(info.get("quality_tier", "mid"))
        note = str(info.get("note", ""))
        score = cost if quality == "budget" else cost * 1.5
        candidates.append((score, model_id, note))

    candidates.sort(key=lambda x: x[0])
    for _, model_id, note in candidates[:3]:
        entry = f"{model_id}"
        if note:
            entry += f" — {note}"
        lines.append(entry)
    return lines


def run_doctor(
    *,
    root: Path | None = None,
    quick: bool = True,
    include_paid: bool = False,
    benchmark: bool = False,
) -> DoctorReport:
    hw = detect_hardware()
    catalog = load_model_catalog()
    settings = get_cheap_llm_settings(root)
    ollama_up = cheap_llm_available(settings)
    installed = fetch_ollama_models(settings.url) if ollama_up else []
    recommended = recommend_models(hw, catalog)
    avoid = recommend_avoid(hw, catalog)

    deprecated_installed = [m.name for m in installed if m.deprecated]
    avoid_installed = [m.name for m in installed if _is_avoided(m.name, avoid)]

    warnings: list[str] = []
    if deprecated_installed:
        warnings.append(
            f"Deprecated models installed: {', '.join(deprecated_installed)} "
            f"→ consider: ollama pull {recommended[0]}"
        )
    if avoid_installed:
        warnings.append(f"Suboptimal models for {hw.tier}: {', '.join(avoid_installed)}")
    if not ollama_up:
        warnings.append(f"Ollama unavailable at {settings.url}")
    if settings.model and _is_deprecated(settings.model, catalog)[0]:
        warnings.append(f"Configured model {settings.model!r} is deprecated")

    bench_result: BenchmarkResult | None = None
    if benchmark and ollama_up and recommended:
        bench_result = run_micro_benchmark(recommended[0], quick=quick)

    paid_recs: list[str] = []
    if include_paid:
        try:
            from greedy_token.model_select import get_llm_registry

            reg = get_llm_registry(root)
            enabled = [m.id for m in reg.expensive_models if m.enabled]
            paid_recs = paid_economy_recommendations(enabled or None, profile="classify")
        except (ImportError, ValueError):
            paid_recs = paid_economy_recommendations(profile="classify")

    return DoctorReport(
        hardware=hw,
        ollama_available=ollama_up,
        ollama_url=settings.url,
        installed=installed,
        configured_model=settings.model,
        recommended=recommended,
        deprecated_installed=deprecated_installed,
        avoid_installed=avoid_installed,
        benchmark=bench_result,
        paid_recommendations=paid_recs,
        warnings=warnings,
    )


def format_doctor_report(report: DoctorReport, *, include_paid: bool = False) -> str:
    hw = report.hardware
    lines = [
        "== greedy-token doctor ==",
        "",
        "Hardware",
        f"  tier:      {hw.tier}",
        f"  RAM:       {hw.ram_gb_available:.1f} / {hw.ram_gb_total:.1f} GB",
        f"  VRAM est:  {hw.vram_gb:.1f} GB ({hw.gpu_name})",
        f"  CPU cores: {hw.cpu_cores}",
        "",
        "Ollama",
        f"  url:       {report.ollama_url}",
        f"  status:    {'available' if report.ollama_available else 'unavailable'}",
        f"  configured: {report.configured_model}",
    ]
    if report.installed:
        lines.append("  installed:")
        for m in report.installed:
            flag = ""
            if m.deprecated:
                flag = " [deprecated]"
            lines.append(f"    - {m.name} ({m.size_bytes // (1024**2)} MB){flag}")
    else:
        lines.append("  installed: (none)")

    lines.extend(["", "Recommendations"])
    for rec in report.recommended:
        lines.append(f"  pull: ollama pull {rec}")

    if report.benchmark and report.benchmark.ok:
        b = report.benchmark
        lines.extend(["", f"Benchmark ({b.model})", f"  latency: {b.latency_ms} ms", f"  tokens:  {b.eval_tokens}"])
    elif report.benchmark and not report.benchmark.ok:
        lines.append(f"\nBenchmark failed: {report.benchmark.error}")

    if include_paid and report.paid_recommendations:
        lines.extend(["", "Paid model economy (classify profile)"])
        for rec in report.paid_recommendations:
            lines.append(f"  - {rec}")

    if report.warnings:
        lines.extend(["", "Warnings"])
        for w in report.warnings:
            lines.append(f"  ⚠ {w}")

    return "\n".join(lines)


def apply_doctor_config(*, recommended_model: str | None = None, force: bool = False) -> Path:
    """Update user config cheap_llm.model with doctor recommendation."""
    from greedy_token.settings import init_user_config, user_config_path

    report = run_doctor(quick=True)
    model = recommended_model or (report.recommended[0] if report.recommended else None)
    if not model:
        raise ValueError("No model recommendation available")

    path = user_config_path()
    if path.is_file() and not force:
        import yaml

        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cheap = cfg.setdefault("cheap_llm", {})
        if isinstance(cheap, dict):
            cheap["model"] = model
        ollama = cfg.setdefault("ollama", {})
        if isinstance(ollama, dict):
            ollama["model"] = model
        path.write_text(yaml.safe_dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        return path

    return init_user_config(model=model, force=force)


def local_health_line() -> str:
    """One-line local model health for footers."""
    try:
        report = run_doctor(quick=True)
        if not report.ollama_available:
            return f"Local: unavailable ({report.ollama_url})"
        if report.deprecated_installed:
            dep = report.deprecated_installed[0]
            rec = report.recommended[0] if report.recommended else "qwen2.5-coder:7b"
            return f"Local: {report.configured_model} · {dep} deprecated → pull {rec}"
        return f"Local: {report.configured_model} OK"
    except (OSError, ValueError, RuntimeError):
        return "Local: probe skipped"
