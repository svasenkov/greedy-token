"""Durable metered-spend ledger — billing records independent of telemetry.

The usage log is telemetry: ``GREEDY_TOKEN_LOG=0`` suppresses it and rotation
evicts old rows.  Neither may decide whether a paid provider call is still
within budget — a hard cap that forgets paid calls is not a cap.  Metered
spend therefore has its own append-only ledger:

* ``reserve`` — written before every metered provider call, inside the same
  cross-process lock that evaluated the cap, so a concurrent caller (or the
  next candidate in one escalation chain) sees the pending estimated spend;
* ``settle`` — written when the call returns, carrying the actual cost;
* ``release`` — written when the call never produced a billable response.

Pending reservations count at their estimate while fresh
(``GREEDY_SPEND_RESERVATION_TTL_SEC``, default 600s); older ones are treated
as crashed calls and expire.  The file is intentionally never rotated.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_RESERVATION_TTL_SEC = 600
_DEFAULT_DIR = Path.home() / ".greedy-token"
_local_lock = threading.Lock()


def spend_log_path() -> Path:
    raw = os.environ.get("GREEDY_TOKEN_SPEND_LOG", "").strip()
    if raw and raw not in ("0", "false", "off", "no"):
        return Path(raw).expanduser()
    home = os.environ.get("GREEDY_TOKEN_HOME", "").strip()
    base = Path(home).expanduser() if home else _DEFAULT_DIR
    return base / "spend.jsonl"


def reservation_ttl_sec() -> float:
    # equivalent: a "XXXX" env-get default and `if raw or True` both still
    # route an unset/empty env through float("") → ValueError → default TTL —
    # the except branch makes them unobservable.
    raw = os.environ.get("GREEDY_SPEND_RESERVATION_TTL_SEC", "").strip()
    try:
        return max(0.0, float(raw)) if raw else float(DEFAULT_RESERVATION_TTL_SEC)
    except ValueError:
        return float(DEFAULT_RESERVATION_TTL_SEC)


@contextmanager
def _fcntl_lock(lock_path: Path) -> Iterator[None]:
    """POSIX ``flock`` on the ``.lock`` sidecar; absent fcntl → in-process only."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def _msvcrt_lock(lock_path: Path) -> Iterator[None]:
    """Windows byte-range lock on the ``.lock`` sidecar.

    ``msvcrt.locking(LK_LOCK)`` retries for ~10s and then raises ``OSError`` —
    callers treat that like any other lock failure (fail closed).
    Absent msvcrt → in-process only.
    """
    try:
        import msvcrt
    except ImportError:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as fh:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def spend_lock() -> Iterator[None]:
    """Serialize check+reserve across processes where an OS file lock exists.

    POSIX holds ``fcntl.flock``; Windows holds a one-byte ``msvcrt.locking``
    claim on the same ``.lock`` sidecar.  Platforms with neither keep only the
    in-process lock — cross-process cap enforcement there is documented
    best-effort (ADR-0002).
    """
    with _local_lock:
        lock_path = spend_log_path().with_suffix(".lock")
        locker = _msvcrt_lock if sys.platform == "win32" else _fcntl_lock
        with locker(lock_path):
            yield


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_ts(value: object) -> datetime | None:
    # equivalent: default "" vs "XXXX" only when ts key is absent — str() of
    # any default still fails fromisoformat → same None/skip branch.
    raw = str(value or "")
    if not raw:
        return None
    try:
        # equivalent: a junk replace pattern ("XXZXX") never matches, and
        # Python ≥3.12 (pinned requires-python) parses the "Z" suffix
        # natively — skipping the replace leaves timestamps identical.
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _finite(value: object) -> float:
    try:
        cost = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    # equivalent: `> 0` vs `>= 0` differ only at cost == 0, where returning
    # cost and returning 0.0 are the same value.
    return cost if math.isfinite(cost) and cost > 0 else 0.0


def _tail_is_partial(path: Path) -> bool:
    """True when *path* exists, is non-empty, and does not end in ``\\n``."""
    try:
        with path.open("rb") as fh:
            if fh.seek(0, os.SEEK_END) == 0:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def _append_spend_record(record: dict) -> None:
    path = spend_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # equivalent: ensure_ascii=None is falsy, so json.dumps takes the same
    # non-escaping path as ensure_ascii=False.
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    # newline="" pins LF bytes — text mode would translate to CRLF on Windows.
    # equivalent: a codec-name case-flip ("UTF-8") is the same codec on every
    # host (codecs.lookup normalizes case).  A dropped/defaulted encoding is
    # NOT equivalent — killed by tests/test_portability.py's ASCII-locale
    # child, which mutmut cannot attribute to this function (subprocess
    # coverage), so the mutant reports survived.  newline=None vs "" is
    # equivalent on the POSIX hosts where campaigns run — both translate to
    # the same \n bytes; only Windows (never mutated on) diverges.
    with path.open("a", encoding="utf-8", newline="") as fh:
        if _tail_is_partial(path):
            fh.write("\n")
        fh.write(line + "\n")


def reserve_spend(
    *,
    reservation_id: str,
    model_id: str,
    est_usd: float,
    operation_id: str = "",
    billing_tier: str = "",
) -> None:
    """Write a pending-spend row. Callers hold ``spend_lock`` when the cap
    decision must be atomic with the reservation."""
    _append_spend_record(
        {
            "ts": _utc_now_iso(),
            "kind": "reserve",
            "id": reservation_id,
            "model_id": model_id,
            "op": operation_id,
            "est_usd": _finite(est_usd),
            "billing_tier": billing_tier,
        }
    )


def settle_spend(reservation_id: str, *, cost_usd: float) -> None:
    try:
        with spend_lock():
            _append_spend_record(
                {
                    "ts": _utc_now_iso(),
                    "kind": "settle",
                    "id": reservation_id,
                    "cost_usd": _finite(cost_usd),
                }
            )
    except OSError as exc:
        print(f"greedy-token: spend ledger write failed: {exc}", file=sys.stderr)


def release_spend(reservation_id: str) -> None:
    try:
        with spend_lock():
            # equivalent: a release outcome is only gated on
            # kind == "settle" in ledger_spend_by_tier — its ts key is never
            # parsed, so renaming it changes nothing observable.
            _append_spend_record(
                {"ts": _utc_now_iso(), "kind": "release", "id": reservation_id}
            )
    except OSError as exc:
        print(f"greedy-token: spend ledger write failed: {exc}", file=sys.stderr)


def _iter_spend_records() -> Iterator[dict]:
    path = spend_log_path()
    if not path.is_file():
        return
    try:
        # equivalent: a codec-name case-flip ("UTF-8") is the same codec on
        # every host (codecs.lookup normalizes case).  A dropped/defaulted
        # encoding is NOT equivalent — killed by tests/test_portability.py's
        # ASCII-locale child, which mutmut cannot attribute to this function
        # (subprocess coverage), so the mutant reports survived.
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield record


def _in_window(ts: datetime | None, since: datetime | None) -> bool:
    return ts is not None and (since is None or ts >= since)


def ledger_spend_usd(
    *,
    since: datetime | None = None,
    include_pending: bool = True,
) -> float:
    """Spend recorded in spend.jsonl: settled actuals plus fresh pending
    reservations (at estimate). Released and stale-pending rows contribute 0."""
    split = ledger_spend_by_tier(since=since, include_pending=include_pending)
    return split["cheap"] + split["expensive"]


def _ledger_spend_detail(
    *,
    since: datetime | None,
    include_pending: bool,
    window: timedelta | None,
    now: datetime,
) -> tuple[dict[str, float], datetime | None]:
    """Totals plus the earliest moment they can drift without a ledger write.

    ``next_change`` bounds how long a caller may reuse the result: a counted
    pending reservation stops counting at ``reserved_at + ttl``; under a
    sliding ``window`` (rolling budget period) counted rows also age out at
    ``ts + window``.  ``None`` means the totals are stable until the ledger
    file itself changes.
    """
    reserves: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    for record in _iter_spend_records():
        kind = record.get("kind")
        rid = str(record.get("id", ""))
        if not rid:
            continue
        if kind == "reserve":
            reserves[rid] = record
        elif kind in ("settle", "release"):
            outcomes[rid] = record

    ttl = reservation_ttl_sec()
    totals = {"cheap": 0.0, "expensive": 0.0}
    changes: list[datetime] = []
    for rid, reserve in reserves.items():
        tier = "cheap" if reserve.get("billing_tier") == "cheap" else "expensive"
        outcome = outcomes.get(rid)
        if outcome is not None:
            outcome_ts = _parse_ts(outcome.get("ts"))
            if outcome.get("kind") == "settle" and _in_window(outcome_ts, since):
                totals[tier] += _finite(outcome.get("cost_usd"))
                if window is not None:
                    # _in_window guarantees outcome_ts is set here.
                    changes.append(outcome_ts + window)
            continue
        if not include_pending:
            continue
        reserved_at = _parse_ts(reserve.get("ts"))
        if reserved_at is None or (now - reserved_at).total_seconds() > ttl:
            continue
        if _in_window(reserved_at, since):
            totals[tier] += _finite(reserve.get("est_usd"))
            expiry = reserved_at + timedelta(seconds=ttl)
            if window is not None:
                expiry = min(expiry, reserved_at + window)
            changes.append(expiry)
    return totals, min(changes) if changes else None


def ledger_spend_by_tier(
    *,
    since: datetime | None = None,
    include_pending: bool = True,
) -> dict[str, float]:
    """Ledger spend split by the derived billing tier recorded at reserve time.

    Rows without a tier (written before the field existed) count as
    "expensive" — the conservative side of the split.
    """
    totals, _next_change = _ledger_spend_detail(
        since=since,
        include_pending=include_pending,
        window=None,
        now=datetime.now(UTC),
    )
    return totals


def is_metered_spend_event(event: dict) -> bool:
    """Metered spend event: v2 billing block tier "metered" (ADR-0002) or the
    legacy marker billing_tier == "expensive" (pre-v2 events had no block)."""
    billing = event.get("billing")
    # equivalent: default "" vs None/dropped/"XXXX" only when tier key is
    # absent; str(...) of any default never equals "metered" → same False
    # branch.
    if isinstance(billing, dict) and str(billing.get("tier", "")).strip().lower() == "metered":
        return True
    return event.get("billing_tier") == "expensive"


def usage_metered_spend_usd(
    *,
    since: datetime | None = None,
    log: Path | None = None,
) -> float:
    """Metered spend still owned by the usage log: events written before the
    spend ledger existed. Events carrying ``spend_ref`` are already accounted
    in spend.jsonl and must not be counted twice."""
    from greedy_token.usage import log_archive_paths, log_path

    total = 0.0
    for path in log_archive_paths(log or log_path()):
        if not path.is_file():
            continue
        try:
            # equivalent: a codec-name case-flip ("UTF-8") is the same codec
            # on every host.  A dropped/defaulted encoding is NOT equivalent —
            # a non-UTF-8 locale (Windows ANSI, LC_ALL=C) decodes differently
            # or raises; killed by tests/test_portability.py locale cases.
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("spend_ref"):
                continue
            if not _in_window(_parse_ts(event.get("ts")), since):
                continue
            if not is_metered_spend_event(event):
                continue
            # v2 events carry the metered USD inside the billing block; legacy
            # expensive events only have the top-level cost_usd.
            billing = event.get("billing")
            block_cost = billing.get("cost_usd") if isinstance(billing, dict) else None
            total += _finite(block_cost if block_cost is not None else event.get("cost_usd"))
    return total


def metered_spend_usd(
    *,
    since: datetime | None = None,
    include_pending: bool = True,
) -> float:
    """Everything the caps must see: durable ledger spend plus pre-ledger
    usage events (deduplicated by ``spend_ref``)."""
    return ledger_spend_usd(since=since, include_pending=include_pending) + (
        usage_metered_spend_usd(since=since)
    )
