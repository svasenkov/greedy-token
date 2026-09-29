"""Spend ledger: durable metered-spend accounting independent of telemetry.

Regression coverage for the audit finding that the hard cap read persisted
telemetry after each call — so intra-chain and concurrent calls could both
pass a cap they exceeded together, and GREEDY_TOKEN_LOG=0 disabled billing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import allure
from greedy_token import spend_guard, spend_ledger
from greedy_token.model_select import ModelSpec
from greedy_token.spend_ledger import (
    ledger_spend_by_tier,
    ledger_spend_usd,
    metered_spend_usd,
    release_spend,
    reservation_ttl_sec,
    reserve_spend,
    settle_spend,
    spend_log_path,
    usage_metered_spend_usd,
)

pytestmark = [
    allure.epic("Spend guard"),
    allure.parent_suite("Spend guard"),
    allure.feature("Metered bulk tier (ADR-0002)"),
    allure.suite("Spend ledger"),
]


def _spec(**kw) -> ModelSpec:
    base = {
        "id": "bulk",
        "enabled": True,
        "provider": "openai_compat",
        "url": "https://x",
        "model": "m",
        "profiles": ("*",),
        "locality": "remote",
        "billing": "metered",
        "cost_per_1m_usd": 0.2,
    }
    base.update(kw)
    return ModelSpec(**base)  # type: ignore[arg-type]


@pytest.fixture
def metered_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "greedy_token.settings.user_config_path", lambda: tmp_path / "missing.yaml"
    )
    monkeypatch.setattr(
        "greedy_token.model_select.user_config_path", lambda: tmp_path / "missing.yaml"
    )
    monkeypatch.setenv("GREEDY_TOKEN_ROOT", str(tmp_path))
    monkeypatch.setenv("GREEDY_TOKEN_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    (tmp_path / ".greedy-token.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "metered": {"opt_in": True},
                    "expensive": {"daily_cap_usd": 0.15, "opt_in": True},
                    "cheap": {
                        "models": [
                            {
                                "id": "bulk",
                                "enabled": True,
                                "model": "m",
                                "profiles": ["p"],
                                "billing": "metered",
                                "cost_per_1m_usd": 0.2,
                            }
                        ]
                    },
                    "escalation": {"enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "phase-manifest.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _usage_event(*, cost: float, spend_ref: str | None = None, ts: str | None = None) -> dict:
    event = {
        "ts": ts or datetime.now(UTC).isoformat(),
        "cost_usd": cost,
        "billing": {"tier": "metered", "cost_usd": cost},
    }
    if spend_ref:
        event["spend_ref"] = spend_ref
    return event


@allure.title("reserve→settle counts actual cost once; pending counts the estimate")
def test_reserve_settle_once(metered_root: Path) -> None:
    reserve_spend(reservation_id="r1", model_id="bulk", est_usd=0.05)
    assert ledger_spend_usd() == pytest.approx(0.05)  # pending at estimate
    settle_spend("r1", cost_usd=0.07)
    # Settled at actual — not est+actual and not pending anymore.
    assert ledger_spend_usd() == pytest.approx(0.07)
    assert ledger_spend_usd(include_pending=False) == pytest.approx(0.07)


@allure.title("release frees a reservation; stale pending reservations expire")
def test_release_and_stale_pending(metered_root: Path) -> None:
    reserve_spend(reservation_id="r2", model_id="bulk", est_usd=0.05)
    release_spend("r2")
    assert ledger_spend_usd() == 0.0

    old_ts = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    with spend_log_path().open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": old_ts,
                    "kind": "reserve",
                    "id": "stale1",
                    "model_id": "bulk",
                    "est_usd": 9.0,
                }
            )
            + "\n"
        )
    assert ledger_spend_usd() == 0.0
    assert ledger_spend_usd(include_pending=False) == 0.0


@allure.title("usage events carrying spend_ref are not double counted")
def test_spend_ref_dedup(metered_root: Path) -> None:
    usage = metered_root / "usage.jsonl"
    usage.write_text(json.dumps(_usage_event(cost=0.1, spend_ref="r3")) + "\n", encoding="utf-8")
    reserve_spend(reservation_id="r3", model_id="bulk", est_usd=0.1)
    settle_spend("r3", cost_usd=0.1)
    # The usage event is the ledger's mirror — 0.1 total, not 0.2.
    assert metered_spend_usd() == pytest.approx(0.1)

    with usage.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_usage_event(cost=0.02)) + "\n")
    # A pre-ledger usage event without spend_ref still counts via usage side.
    assert metered_spend_usd() == pytest.approx(0.12)


@allure.title("usage-side reader skips junk lines, non-metered events and spend_ref rows")
def test_usage_side_skips(metered_root: Path) -> None:
    usage = metered_root / "usage.jsonl"
    today = datetime.now(UTC).isoformat()
    usage.write_text(
        "not-json\n"
        + json.dumps({"ts": today, "billing": {"tier": "cheap"}, "cost_usd": 9}) + "\n"
        + json.dumps({"ts": "1999-01-01T00:00:00Z", "billing": {"tier": "metered"}, "cost_usd": 9}) + "\n"
        + json.dumps({"ts": today, "billing": {"tier": "metered"}, "cost_usd": "bad"}) + "\n"
        + json.dumps(_usage_event(cost=0.4, spend_ref="x")) + "\n"
        + json.dumps(_usage_event(cost=0.25)) + "\n",
        encoding="utf-8",
    )
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # since=None means "all recorded history" — a 1999 row still counts there.
    assert usage_metered_spend_usd() == pytest.approx(9.25)
    assert usage_metered_spend_usd(since=midnight) == pytest.approx(0.25)
    assert usage_metered_spend_usd(log=metered_root / "missing.jsonl") == 0.0


@allure.title("metered accounting persists when GREEDY_TOKEN_LOG=0")
def test_spend_ledger_survives_telemetry_off(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_LOG", "0")
    reserve_spend(reservation_id="r4", model_id="bulk", est_usd=0.03)
    settle_spend("r4", cost_usd=0.03)
    assert ledger_spend_usd() == pytest.approx(0.03)


@allure.title("a pending reservation blocks the second call over the cap")
def test_reserve_blocks_concurrent_call(metered_root: Path) -> None:
    spec = _spec()
    first = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.1)
    assert first.allowed
    assert first.reservation_id

    # 0.1 pending + 0.06 estimated > 0.15 daily cap → denied before any HTTP.
    second = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.06)
    assert not second.allowed
    assert "cap" in second.reason

    first.release()
    third = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.06)
    assert third.allowed
    third.release()


@allure.title("settled spend of the first call blocks the next candidate")
def test_settled_spend_blocks_next_call(metered_root: Path) -> None:
    spec = _spec()
    first = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.01)
    assert first.allowed
    first.settle(0.1)
    second = spend_guard.reserve_metered_call(spec, root=metered_root, est_cost_usd=0.06)
    assert not second.allowed
    assert "cap" in second.reason


@allure.title("unknown or non-finite price is denied before opt-in")
def test_unknown_price_denied(metered_root: Path) -> None:
    for bad in (None, 0.0, -1.0, float("nan"), float("inf")):
        spec = _spec(cost_per_1m_usd=bad)
        decision = spend_guard.check_metered_allowed(spec, root=metered_root, cli_allow=True)
        assert not decision.allowed, bad
        assert "price" in decision.reason


@allure.title("ledger spend splits by the billing tier recorded at reserve time")
def test_ledger_tier_split(metered_root: Path) -> None:
    reserve_spend(reservation_id="c1", model_id="bulk", est_usd=0.01, billing_tier="cheap")
    settle_spend("c1", cost_usd=0.02)
    reserve_spend(reservation_id="e1", model_id="big", est_usd=0.03, billing_tier="expensive")
    settle_spend("e1", cost_usd=0.03)
    reserve_spend(reservation_id="u1", model_id="old", est_usd=0.04)  # no tier → expensive

    split = ledger_spend_by_tier()
    assert split["cheap"] == pytest.approx(0.02)
    assert split["expensive"] == pytest.approx(0.03 + 0.04)


@allure.title("free (non-metered) models get a no-op reservation")
def test_free_model_no_reservation(metered_root: Path) -> None:
    spec = _spec(billing="free", cost_per_1m_usd=None)
    reservation = spend_guard.reserve_metered_call(spec, root=metered_root)
    assert reservation.allowed
    assert not reservation.reservation_id
    reservation.settle(9.9)  # no-op — nothing was reserved
    reservation.release()
    assert ledger_spend_usd() == 0.0


@allure.title("spend_log_path: env override wins, GREEDY_TOKEN_HOME is the default base")
def test_spend_log_path(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    assert spend_log_path() == metered_root / "spend.jsonl"
    monkeypatch.delenv("GREEDY_TOKEN_SPEND_LOG")
    home = Path(os.environ["GREEDY_TOKEN_HOME"])
    assert spend_log_path() == home / "spend.jsonl"


@allure.title("an unparseable GREEDY_SPEND_RESERVATION_TTL_SEC falls back to the default")
def test_reservation_ttl_bad_env(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_SPEND_RESERVATION_TTL_SEC", "not-a-number")
    assert reservation_ttl_sec() == 600.0
    monkeypatch.setenv("GREEDY_SPEND_RESERVATION_TTL_SEC", "")
    assert reservation_ttl_sec() == 600.0
    monkeypatch.setenv("GREEDY_SPEND_RESERVATION_TTL_SEC", "30")
    assert reservation_ttl_sec() == 30.0


@allure.title("the ledger reader skips junk rows and still counts the valid tail")
def test_iter_spend_records_skips_junk(metered_root: Path) -> None:
    # Every skip-triggering row precedes a valid one, so a continue→break
    # mutation on any filter undercounts the ledger.
    now = datetime.now(UTC).isoformat()
    spend_log_path().write_text(
        "\n"  # blank line → `if not line`
        "not-json\n"  # JSONDecodeError → continue
        "[1, 2]\n"  # valid JSON but not a dict → continue
        + json.dumps({"kind": "reserve", "est_usd": 9.0}) + "\n"  # no id → continue
        + json.dumps({"kind": "audit", "id": "q1", "est_usd": 9.0}) + "\n"  # unknown kind
        + json.dumps({"kind": "reserve", "id": "no-ts", "est_usd": 9.0}) + "\n"  # ts absent → stale
        + json.dumps({"kind": "reserve", "id": "bad-ts", "ts": "junk", "est_usd": 9.0}) + "\n"
        + json.dumps({"kind": "reserve", "id": "no-outcome-ts", "ts": now, "est_usd": 9.0}) + "\n"
        + json.dumps({"kind": "settle", "id": "no-outcome-ts"}) + "\n"  # settle without ts → not in window
        + json.dumps({"kind": "reserve", "id": "ok", "ts": now, "est_usd": 0.5}) + "\n",
        encoding="utf-8",
    )
    assert ledger_spend_usd() == pytest.approx(0.5)


@allure.title("a fresh pending reservation outside the since window does not count")
def test_pending_reserve_outside_since_window(metered_root: Path) -> None:
    # Within the 600s TTL (fresh) but before `since` — skipped by the window
    # check, not by staleness.
    old_fresh = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with spend_log_path().open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"kind": "reserve", "id": "old", "ts": old_fresh, "est_usd": 3.0}
            )
            + "\n"
        )
    reserve_spend(reservation_id="recent", model_id="bulk", est_usd=5.0)
    since = datetime.now(UTC) - timedelta(minutes=2)
    assert ledger_spend_usd(since=since) == pytest.approx(5.0)


@allure.title("an unreadable ledger file reads as zero, not as a crash")
def test_iter_spend_records_unreadable(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = spend_log_path()
    target.write_text(
        json.dumps({"kind": "reserve", "id": "r", "ts": datetime.now(UTC).isoformat(), "est_usd": 7.0})
        + "\n",
        encoding="utf-8",
    )
    real_read_text = Path.read_text

    def guarded(self: Path, *args: object, **kwargs: object) -> str:
        if self == target:
            raise OSError("read denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    assert ledger_spend_usd() == 0.0


@allure.title("unwritable ledger rows warn on stderr instead of crashing the call path")
def test_settle_release_write_failure_warns(
    metered_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A directory named spend.jsonl: open("a") fails on every OS (IsADirectory
    # on POSIX, PermissionError on Windows) while the .lock sidecar still works.
    spend_log_path().mkdir()
    settle_spend("r9", cost_usd=1.0)
    release_spend("r9")
    err = capsys.readouterr().err
    assert err.count("spend ledger write failed") == 2


@allure.title("a reservation that cannot be persisted fails the cap check closed")
def test_unwritable_ledger_fails_closed(metered_root: Path) -> None:
    spend_log_path().mkdir()
    reservation = spend_guard.reserve_metered_call(_spec(), root=metered_root, est_cost_usd=0.01)
    assert not reservation.allowed
    assert "spend ledger write failed" in reservation.reason


@allure.title("an unreadable usage archive is skipped, later archives still count")
def test_usage_metered_archive_unreadable(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    usage = metered_root / "usage.jsonl"
    bad_archive = usage.with_name("usage.jsonl.1")
    later_archive = usage.with_name("usage.jsonl.2")
    usage.write_text(json.dumps(_usage_event(cost=0.2)) + "\n", encoding="utf-8")
    bad_archive.write_text(json.dumps(_usage_event(cost=9.0)) + "\n", encoding="utf-8")
    # A valid archive after the unreadable one kills a continue→break mutant.
    later_archive.write_text(json.dumps(_usage_event(cost=0.3)) + "\n", encoding="utf-8")
    real_read_text = Path.read_text

    def guarded(self: Path, *args: object, **kwargs: object) -> str:
        if self == bad_archive:
            raise OSError("read denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    assert usage_metered_spend_usd() == pytest.approx(0.5)


# --------------------------------------------------------------------------
# Mutation-kill coverage — assertions a surviving mutant cannot satisfy.
# --------------------------------------------------------------------------


@allure.title("GREEDY_TOKEN_SPEND_LOG disable tokens route to the home default")
@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_spend_log_path_disable_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(tmp_path))
    monkeypatch.setenv("GREEDY_TOKEN_SPEND_LOG", value)
    assert spend_log_path() == tmp_path / "spend.jsonl"


@allure.title("no env vars at all → ~/.greedy-token/spend.jsonl")
def test_spend_log_path_full_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREEDY_TOKEN_SPEND_LOG", raising=False)
    monkeypatch.delenv("GREEDY_TOKEN_HOME", raising=False)
    assert spend_log_path() == Path.home() / ".greedy-token" / "spend.jsonl"


@allure.title("explicit zero TTL is honoured — not clamped to a positive floor")
def test_reservation_ttl_zero(metered_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREEDY_SPEND_RESERVATION_TTL_SEC", "0")
    assert reservation_ttl_sec() == 0.0
    monkeypatch.setenv("GREEDY_SPEND_RESERVATION_TTL_SEC", "0.5")
    assert reservation_ttl_sec() == 0.5


@allure.title("reserve rows carry UTC-aware ts and the full key schema")
def test_reserve_record_schema(metered_root: Path) -> None:
    reserve_spend(reservation_id="schema", model_id="модель", est_usd=0.01)
    rec = json.loads(spend_log_path().read_text(encoding="utf-8").strip())
    assert rec["kind"] == "reserve" and rec["id"] == "schema"
    assert rec["model_id"] == "модель" and rec["op"] == "" and rec["billing_tier"] == ""
    assert rec["est_usd"] == pytest.approx(0.01)
    assert datetime.fromisoformat(rec["ts"]).tzinfo is not None


@allure.title("spend rows are compact UTF-8 JSONL — Cyrillic is not escaped")
def test_spend_record_compact_utf8(metered_root: Path) -> None:
    reserve_spend(reservation_id="enc", model_id="модель-x", est_usd=0.01)
    raw = spend_log_path().read_bytes()
    assert "модель-x".encode() in raw
    assert b'", "' not in raw and b'": "' not in raw


@allure.title("a nested missing GREEDY_TOKEN_HOME is created for the ledger")
def test_reserve_creates_missing_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "deep" / "nested" / "home"
    monkeypatch.setenv("GREEDY_TOKEN_HOME", str(home))
    monkeypatch.delenv("GREEDY_TOKEN_SPEND_LOG", raising=False)
    reserve_spend(reservation_id="deep", model_id="m", est_usd=0.01)
    assert (home / "spend.jsonl").is_file()


@allure.title("a fresh reserve row without an id never counts")
def test_ledger_row_without_id_skipped(metered_root: Path) -> None:
    now = datetime.now(UTC).isoformat()
    spend_log_path().write_text(
        json.dumps({"kind": "reserve", "ts": now, "est_usd": 9.0}) + "\n",
        encoding="utf-8",
    )
    assert ledger_spend_usd() == 0.0


@allure.title("settled rows outside the since window do not count")
def test_ledger_settle_outside_window(metered_root: Path) -> None:
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    spend_log_path().write_text(
        json.dumps({"kind": "reserve", "id": "w1", "ts": old, "est_usd": 1.0}) + "\n"
        + json.dumps({"kind": "settle", "id": "w1", "ts": old, "cost_usd": 1.0}) + "\n",
        encoding="utf-8",
    )
    since = datetime.now(UTC) - timedelta(hours=1)
    assert ledger_spend_usd(since=since) == 0.0


@allure.title("two settled calls in one tier accumulate, not overwrite")
def test_ledger_settles_accumulate(metered_root: Path) -> None:
    reserve_spend(reservation_id="a1", model_id="m", est_usd=0.1)
    settle_spend("a1", cost_usd=0.1)
    reserve_spend(reservation_id="a2", model_id="m", est_usd=0.2)
    settle_spend("a2", cost_usd=0.2)
    assert ledger_spend_usd() == pytest.approx(0.3)


@allure.title("include_pending=False still scans every later settled row")
def test_ledger_include_pending_false_scans_all(metered_root: Path) -> None:
    now = datetime.now(UTC).isoformat()
    spend_log_path().write_text(
        json.dumps({"kind": "reserve", "id": "pend", "ts": now, "est_usd": 9.0}) + "\n"
        + json.dumps({"kind": "reserve", "id": "done", "ts": now, "est_usd": 0.5}) + "\n"
        + json.dumps({"kind": "settle", "id": "done", "ts": now, "cost_usd": 0.5}) + "\n",
        encoding="utf-8",
    )
    assert ledger_spend_usd(include_pending=False) == pytest.approx(0.5)


@allure.title("a pending reservation exactly at the TTL boundary still counts")
def test_ledger_pending_at_ttl_boundary(
    metered_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GREEDY_SPEND_RESERVATION_TTL_SEC", "600")
    fixed = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(spend_ledger, "datetime", _FrozenDatetime)
    boundary = (fixed - timedelta(seconds=600)).isoformat()
    spend_log_path().write_text(
        json.dumps({"kind": "reserve", "id": "edge", "ts": boundary, "est_usd": 2.0})
        + "\n",
        encoding="utf-8",
    )
    # `> ttl` keeps the row, `>= ttl` drops it.
    assert ledger_spend_usd() == pytest.approx(2.0)


@allure.title("pending reservations count in metered_spend_usd by default")
def test_metered_spend_default_counts_pending(metered_root: Path) -> None:
    reserve_spend(reservation_id="mp", model_id="bulk", est_usd=0.4)
    assert metered_spend_usd() == pytest.approx(0.4)


@allure.title("metered spend applies the since window to the ledger side too")
def test_metered_spend_since_window(metered_root: Path) -> None:
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    spend_log_path().write_text(
        json.dumps({"kind": "reserve", "id": "ms", "ts": old, "est_usd": 1.0}) + "\n"
        + json.dumps({"kind": "settle", "id": "ms", "ts": old, "cost_usd": 1.0}) + "\n",
        encoding="utf-8",
    )
    since = datetime.now(UTC) - timedelta(hours=1)
    assert metered_spend_usd(since=since) == 0.0


@allure.title("include_pending=False drops pending rows entirely")
def test_ledger_spend_usd_exclude_pending(metered_root: Path) -> None:
    reserve_spend(reservation_id="xp", model_id="bulk", est_usd=7.0)
    assert ledger_spend_usd(include_pending=False) == 0.0


@allure.title("non-finite ledger amounts contribute zero, not inf")
def test_ledger_ignores_infinite_amounts(metered_root: Path) -> None:
    now = datetime.now(UTC).isoformat()
    spend_log_path().write_text(
        '{"kind": "reserve", "id": "inf", "ts": "' + now + '", "est_usd": Infinity}\n'
        + '{"kind": "reserve", "id": "inf2", "ts": "' + now + '", "est_usd": 1.0}\n'
        + '{"kind": "settle", "id": "inf2", "ts": "' + now + '", "cost_usd": Infinity}\n',
        encoding="utf-8",
    )
    assert ledger_spend_usd() == 0.0


@allure.title("a lowercase-z timestamp is rejected, not silently parsed")
def test_ledger_lowercase_z_ts_rejected(metered_root: Path) -> None:
    # Fresh ts: the row must clear the TTL filter and die on `_parse_ts` alone —
    # a stale row would be skipped for a different reason and miss the mutant.
    fresh_z = datetime.now(UTC).isoformat().replace("+00:00", "z")
    spend_log_path().write_text(
        json.dumps({"kind": "reserve", "id": "lz", "ts": fresh_z, "est_usd": 9.0})
        + "\n",
        encoding="utf-8",
    )
    assert ledger_spend_usd() == 0.0


@allure.title("metered_spend_usd honours include_pending=False")
def test_metered_spend_excludes_pending(metered_root: Path) -> None:
    reserve_spend(reservation_id="mpf", model_id="bulk", est_usd=6.0)
    assert metered_spend_usd(include_pending=False) == 0.0


@allure.title("a crashed writer's partial tail never swallows the next record")
def test_append_after_partial_tail(metered_root: Path) -> None:
    # A torn last line (crash mid-write) must not merge with the next record —
    # the appender starts a fresh line, keeping both rows readable.
    target = spend_log_path()
    target.write_text('{"kind": "reserve", "id": "cut', encoding="utf-8")
    reserve_spend(reservation_id="r2", model_id="bulk", est_usd=1.5)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith('"cut')
    assert ledger_spend_usd() == pytest.approx(1.5)


@allure.title("_tail_is_partial returns False when the path is unopenable")
def test_tail_is_partial_oserror(tmp_path: Path) -> None:
    assert spend_ledger._tail_is_partial(tmp_path) is False  # directory → OSError
    assert spend_ledger._tail_is_partial(tmp_path / "missing") is False
