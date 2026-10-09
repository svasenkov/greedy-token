"""Filter ripgrep output and cap tool/machine payloads at the output boundary."""

from __future__ import annotations

import copy
import json
from typing import Any

from greedy_token.tokens import count_tokens

JUNK_TOOL_PATH_FRAGMENTS = (
    ".cursor/hooks/",
    ".claude/hooks/",
    ".devin/hooks/",
    "greedy-token-route.sh",
    "greedy-token-home/dev/README",
)


def filter_tool_output(output: str) -> str:
    lines: list[str] = []
    for line in output.splitlines():
        if any(fragment in line for fragment in JUNK_TOOL_PATH_FRAGMENTS):
            continue
        if line.strip():
            lines.append(line)
    return "\n".join(lines).strip()


# Total displayed hits cap — a repo-wide rg can flood the answer with
# self-referential noise; beyond the cap the tail is folded into a marker.
TOOL_OUTPUT_LINE_CAP = 30

# Byte-level safety net for the same boundary: a line cap alone lets a single
# 100 KB line through. The marker text is included in the byte budget.
TOOL_OUTPUT_BYTE_CAP = 16 * 1024

# Opt-in machine-output envelope bounds — apply to the *whole* serialized
# payload (output + metadata + accounting fields), not just the body.
MACHINE_OUTPUT_MAX_BYTES = 16 * 1024
MACHINE_OUTPUT_MAX_TOKENS = 4096

_MARKER_SUFFIX = "…"


# Envelope fields that carry the verdict/accounting contract — never dropped
# wholesale by the structural shrinker, and their *values* are immutable:
# a protected key's value is never shrunk, emptied or descended into.
_PROTECTED_KEYS = frozenset(
    {
        "ok",
        "verdict",
        "outcome",
        "result_status",
        "truncated",
        "count",
        "exit_code",
        "executed",
        "invocable",
        "operation_id",
        "op_id",
        "route_id",
        "target",
        "tier",
        "complexity",
        "est_tokens",
        "error",
        "cap_bytes",
        "cap_tokens",
        "payload_bytes",
        "payload_tokens",
        "dropped_bytes",
        "dropped_items",
        "dropped_keys",
    }
)
_PROTECTED_SUFFIXES = ("_count", "_total", "_dropped", "_bytes", "_tokens")


def _is_protected_key(key: str) -> bool:
    return key in _PROTECTED_KEYS or key.endswith(_PROTECTED_SUFFIXES)


def _has_contract(node: Any) -> bool:
    """True when *node* carries protected contract fields at top level."""
    return isinstance(node, dict) and any(_is_protected_key(k) for k in node)


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _utf8_prefix(text: str, byte_limit: int) -> tuple[str, int]:
    """Cut *text* at a UTF-8 boundary within *byte_limit*; return (kept, dropped)."""
    if byte_limit <= 0:
        return "", len(text.encode("utf-8"))
    raw = text.encode("utf-8")
    if len(raw) <= byte_limit:
        return text, 0
    kept = raw[:byte_limit].decode("utf-8", errors="ignore")
    return kept, len(raw) - len(kept.encode("utf-8"))


def cap_tool_output(
    output: str,
    limit: int = TOOL_OUTPUT_LINE_CAP,
    byte_cap: int = TOOL_OUTPUT_BYTE_CAP,
) -> str:
    """Keep at most *limit* lines and *byte_cap* UTF-8 bytes; overflow → marker."""
    if not output:
        return output
    out = output
    if limit > 0:
        lines = out.splitlines()
        if len(lines) > limit:
            kept = lines[:limit]
            kept.append(
                f"… truncated — {len(lines) - limit} more line(s) (cap {limit})"
            )
            out = "\n".join(kept)
    if byte_cap <= 0:
        return out
    size = len(out.encode("utf-8"))
    if size <= byte_cap:
        return out
    if byte_cap < 96:
        kept, _ = _utf8_prefix(out, byte_cap)
        return kept
    kept, _ = _utf8_prefix(out, byte_cap - 96)
    dropped = size - len(kept.encode("utf-8"))
    return f"{kept}… truncated — {dropped} more byte(s) (cap {byte_cap})"


# ---------------------------------------------------------------------------
# Structural JSON shrinking — never slices serialized JSON or UTF-8 mid-char.
# Order per step: list tails → oversized string fields → non-protected
# compound keys. Every loss is counted on the payload itself.
# ---------------------------------------------------------------------------


def _biggest_list(node: Any) -> tuple[dict, str] | None:
    best = None
    best_size = 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, value in cur.items():
                if _is_protected_key(key):
                    continue  # protected contract values are immutable
                if isinstance(value, list):
                    size = _json_bytes(value)
                    if value and size > best_size:
                        best, best_size = (cur, key), size
                    stack.append(value)
                elif isinstance(value, dict):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(cur)
    return best


def _biggest_string(node: Any) -> tuple[dict, str] | None:
    best = None
    best_size = 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, value in cur.items():
                if _is_protected_key(key):
                    continue  # protected contract values are immutable
                if isinstance(value, str):
                    size = len(value.encode("utf-8"))
                    if size > best_size:
                        best, best_size = (cur, key), size
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(cur)
    return best


def _droppable_keys(doc: dict) -> list[str]:
    """Non-protected keys, largest serialized value first — scalars included."""
    sized = [
        (key, _json_bytes(value))
        for key, value in doc.items()
        if not _is_protected_key(key)
    ]
    sized.sort(key=lambda kv: -kv[1])
    return [key for key, _ in sized]


def _tail_drop_count(items: list, deficit: int) -> int:
    avg = max(1, _json_bytes(items) // max(1, len(items)))
    return max(1, min(len(items), deficit // avg + 1))


def _shrink_text(text: str, allowed: int) -> str:
    """Fit a string field into *allowed* bytes — structurally when it's JSON.

    An originally valid JSON document is never emitted as cut JSON text.
    A document carrying a protected contract reduces only to its skeleton
    (verdict/count values survive verbatim); when even the skeleton exceeds
    *allowed* the field yields ``""`` so the caller drops the key and marks
    the loss via ``error``/``dropped_*`` — never a silent ``{}``. Contract-
    free JSON degrades to a truncation marker; plain text cuts on UTF-8
    boundaries.
    """
    if len(text.encode("utf-8")) <= allowed:
        return text
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            inner = json.loads(stripped)
        except ValueError:
            inner = None
        if isinstance(inner, dict):
            shrunk = _shrink_value(inner, allowed)
            out = json.dumps(shrunk, ensure_ascii=False)
            if len(out.encode("utf-8")) <= allowed:
                return out
            if _has_contract(inner):
                skel = json.dumps(
                    _strip_to_skeleton(shrunk), ensure_ascii=False
                )
                if len(skel.encode("utf-8")) <= allowed:
                    return skel
                return ""
            marker = '{"truncated": true}'
            if len(marker.encode("utf-8")) <= allowed:
                return marker
            return "{}" if allowed >= 2 else ""
        if isinstance(inner, list):
            shrunk = _shrink_value(inner, allowed)
            out = json.dumps(shrunk, ensure_ascii=False)
            if len(out.encode("utf-8")) <= allowed:
                return out
            return "[]" if allowed >= 2 else ""
    elif stripped[:1] == '"':
        # A JSON string document — cut its content as plain text, never
        # leave a dangling opening quote that corrupts the inner contract.
        try:
            inner = json.loads(stripped)
        except ValueError:
            inner = None
        if isinstance(inner, str):
            text = inner
    if allowed <= len(_MARKER_SUFFIX.encode("utf-8")):
        return ""
    kept, _ = _utf8_prefix(text, allowed - len(_MARKER_SUFFIX.encode("utf-8")))
    return kept + _MARKER_SUFFIX


def _shrink_value(node: Any, budget: int) -> Any:
    if isinstance(node, dict):
        shrunk = copy.deepcopy(node)
        shrunk.setdefault("truncated", False)
        _shrink_dict_in_place(shrunk, budget)
        return shrunk
    if isinstance(node, list):
        shrunk = copy.deepcopy(node)
        while shrunk and _json_bytes(shrunk) > budget:
            keep = int(len(shrunk) * budget / max(1, _json_bytes(shrunk)))
            keep = min(keep, len(shrunk) - 1)
            shrunk = shrunk[: max(0, keep)]
        return shrunk
    if isinstance(node, str):
        return _shrink_text(node, budget)
    return node


def _shrink_string_field(
    parent: dict, key: str, *, doc_size: int, budget_bytes: int
) -> int:
    """Shrink one string field; return serialized bytes actually dropped.

    Accounting is in *serialized* bytes — nested-JSON strings expand under
    escaping, so the raw cap is tightened by the measured overshoot until the
    emitted field really fits its share of the budget.
    """
    text = parent[key]
    field_cost = _json_bytes({key: text})
    rest = doc_size - field_cost
    allowed_field = max(0, budget_bytes - rest)
    # serialized/raw expansion rate (JSON escaping inflates nested JSON text);
    # estimate the raw cap by the rate, then tighten by the actual overshoot.
    rate = field_cost / max(1, len(text.encode("utf-8")))
    allowed = int(allowed_field / rate) if rate > 0 else allowed_field
    for _ in range(8):
        new = _shrink_text(text, allowed)
        parent[key] = new
        cost = _json_bytes({key: new})
        if cost <= allowed_field or len(new.encode("utf-8")) == 0:
            break
        if new == text and len(text.encode("utf-8")) <= allowed:
            # Already at/below the raw cap yet still over after escaping —
            # the escape share alone exceeds the field's budget.
            break
        allowed = max(0, allowed - (cost - allowed_field) - 8)
        text = new
    return field_cost - _json_bytes({key: parent[key]})


def _shrink_dict_in_place(doc: dict, budget_bytes: int) -> int:
    """Shrink *doc* in place toward *budget_bytes*; return dropped bytes."""
    dropped = 0
    for _ in range(512):
        size = _json_bytes(doc)
        if size <= budget_bytes:
            break
        doc["truncated"] = True
        deficit = size - budget_bytes
        hit = _biggest_list(doc)
        if hit is not None:
            parent, key = hit
            items = parent[key]
            n = _tail_drop_count(items, deficit)
            tail = items[len(items) - n :]
            parent[key] = items[: len(items) - n]
            dropped += _json_bytes(tail)
            parent[f"{key}_dropped"] = parent.get(f"{key}_dropped", 0) + n
            continue
        hit = _biggest_string(doc)
        if hit is not None:
            parent, key = hit
            gained = _shrink_string_field(
                parent, key, doc_size=size, budget_bytes=budget_bytes
            )
            if gained > 0:
                dropped += gained
                continue
        keys = _droppable_keys(doc)
        if keys:
            n = 0
            for key in keys:
                dropped += _json_bytes(doc.pop(key))
                n += 1
                if _json_bytes(doc) <= budget_bytes:
                    break
            doc["dropped_keys"] = doc.get("dropped_keys", 0) + n
            continue
        break
    return dropped


def _strip_to_skeleton(doc: dict) -> dict:
    """Last-resort contraction: keep protected scalars, drop the rest."""
    skeleton = {
        key: value
        for key, value in doc.items()
        if key in _PROTECTED_KEYS or key.endswith(_PROTECTED_SUFFIXES)
    }
    removed = _json_bytes(doc) - _json_bytes(skeleton)
    skeleton["truncated"] = True
    skeleton["dropped_keys"] = doc.get("dropped_keys", 0) + len(doc) - len(skeleton)
    skeleton["dropped_bytes"] = doc.get("dropped_bytes", 0) + max(0, removed)
    if isinstance(doc.get("error"), dict):
        code = doc["error"].get("code")
        if code is not None:
            skeleton["error"] = {"code": code}
    return skeleton


def shrink_json_payload(
    payload: dict,
    *,
    max_bytes: int = MACHINE_OUTPUT_MAX_BYTES,
    max_tokens: int = MACHINE_OUTPUT_MAX_TOKENS,
    indent: int | None = None,
) -> dict:
    """Deep-copied *payload* whose serialized dump fits the byte/token caps.

    Verdict/accounting scalars are protected; truncation is always marked
    via ``truncated`` and the ``*_dropped``/``dropped_*`` counters. ``indent``
    must match the caller's serialization — the cap is measured on the form
    actually emitted.
    """
    doc = copy.deepcopy(payload)
    doc.setdefault("truncated", False)
    dump_kw: dict[str, Any] = {"ensure_ascii": False}
    if indent is not None:
        dump_kw["indent"] = indent
    for _ in range(16):
        raw = json.dumps(doc, **dump_kw)
        size = len(raw.encode("utf-8"))
        tokens = count_tokens(raw).tokens if max_tokens > 0 else 0
        if size <= max_bytes and tokens <= max_tokens:
            break
        doc["truncated"] = True
        # _shrink_dict_in_place measures the compact form; the emitted
        # (indented) size is larger — translate the cap, plus a margin for
        # counter-digit growth between passes.
        budget = max_bytes - (size - _json_bytes(doc)) - 96
        if max_tokens > 0 and tokens > max_tokens:
            budget = min(budget, max(64, int(size * max_tokens / tokens) - 64))
        gained = _shrink_dict_in_place(doc, max(64, budget))
        if gained <= 0:
            break
        doc["dropped_bytes"] = doc.get("dropped_bytes", 0) + gained
    raw = json.dumps(doc, **dump_kw)
    size = len(raw.encode("utf-8"))
    tokens = count_tokens(raw).tokens if max_tokens > 0 else 0
    if size > max_bytes or tokens > max_tokens:
        # Iterations exhausted / no progress — the protected-only floor is
        # the last representation; if even it exceeds the caps the payload
        # is unrepresentable and the caller must refuse, not emit oversize.
        doc = _strip_to_skeleton(doc)
        raw = json.dumps(doc, **dump_kw)
        size = len(raw.encode("utf-8"))
        tokens = count_tokens(raw).tokens if max_tokens > 0 else 0
        if size > max_bytes or tokens > max_tokens:
            raise ValueError(
                f"payload unrepresentable within {max_bytes}B/"
                f"{max_tokens}tok: protected contract alone is "
                f"{size}B/{tokens}tok"
            )
    return doc


def _inner_floor(text: str) -> str:
    """Minimal valid representation of an ``output`` string.

    A JSON contract document keeps only its protected skeleton; contract-
    free JSON degrades to a truncation marker; plain text keeps the marker.
    """
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            inner = json.loads(stripped)
        except ValueError:
            inner = None
        if isinstance(inner, dict):
            if _has_contract(inner):
                return json.dumps(
                    _strip_to_skeleton(inner), ensure_ascii=False
                )
            return '{"truncated": true}'
        if isinstance(inner, list):
            return "[]"
        return stripped if len(stripped) <= 64 else _MARKER_SUFFIX
    return _MARKER_SUFFIX


def _mandatory_floor(doc: dict) -> dict:
    """Smallest honest doc: protected contract + minimal ``output`` form."""
    floor = _strip_to_skeleton(doc)
    out = doc.get("output")
    if isinstance(out, str) and out:
        floor["output"] = _inner_floor(out)
    # Non-string ``output`` has no minimal form — it either survives the
    # shrinker whole or is dropped and marked lost via _content_lost.
    return floor


def _refusal_doc(doc: dict) -> dict:
    """Delivery-refusal floor: verdict scalars kept, error marks the loss."""
    floor = _strip_to_skeleton(doc)
    floor["ok"] = False
    if not isinstance(floor.get("error"), dict) or not floor["error"].get(
        "code"
    ):
        floor["error"] = {"code": "unrepresentable"}
    return floor


def _content_lost(doc: dict, content_keys: list[str]) -> bool:
    """True when every original content key was dropped or emptied."""
    return bool(content_keys) and all(
        key not in doc or doc[key] in (None, "", [], {})
        for key in content_keys
    )


def _fits_emitted(
    doc: dict, max_bytes: int, max_tokens: int, *, worst_counters: bool = False
) -> bool:
    """Measure the emitted form; ``worst_counters`` sizes payload_* fields
    at their upper-bound digit width before accounting converges."""
    probe = dict(doc)
    if worst_counters:
        probe["payload_bytes"] = max_bytes
        probe["payload_tokens"] = max_tokens
    raw = json.dumps(probe, ensure_ascii=False)
    if len(raw.encode("utf-8")) > max_bytes:
        return False
    return max_tokens <= 0 or count_tokens(raw).tokens <= max_tokens


def _emit_doc(doc: dict, max_bytes: int, max_tokens: int) -> str:
    """Serialize with self-accounting fixpoint; reject any oversize emission."""
    for _ in range(8):
        raw = json.dumps(doc, ensure_ascii=False)
        size = len(raw.encode("utf-8"))
        tokens = count_tokens(raw).tokens
        if (doc.get("payload_bytes"), doc.get("payload_tokens")) == (
            size,
            tokens,
        ):
            if size > max_bytes or (max_tokens > 0 and tokens > max_tokens):
                break
            return raw
        doc["payload_bytes"] = size
        doc["payload_tokens"] = tokens
    raise ValueError(
        f"machine output unrepresentable within {max_bytes}B/"
        f"{max_tokens}tok"
    )


def format_machine_output(
    body: str = "",
    *,
    payload: dict | None = None,
    ok: bool | None = None,
    outcome: str | None = None,
    result_status: str | None = None,
    executed: bool | None = None,
    operation_id: str | None = None,
    error: dict | None = None,
    max_bytes: int = MACHINE_OUTPUT_MAX_BYTES,
    max_tokens: int = MACHINE_OUTPUT_MAX_TOKENS,
) -> str:
    """Serialize one machine-mode tool response — JSON envelope, no footer.

    The cap covers the entire serialized payload: ``output``, metadata and
    the cap/accounting fields themselves. ``payload`` keys take precedence
    over keyword fallbacks; a refusal is expressed via ``error`` while a
    negative terminal verdict keeps ``ok=False`` + ``outcome``/``result_status``.
    Protected contract *values* are immutable — never shrunk or emptied.
    When the mandatory contract cannot be represented within the caps the
    call fails closed: a bounded ``error.code == "unrepresentable"`` refusal
    envelope when it fits, ``ValueError`` when even that exceeds the cap —
    never a silent oversize, emptied status, or cut-JSON ``output``.
    """
    if payload is not None:
        doc = copy.deepcopy(payload)
        if error is not None:
            doc.setdefault("error", error)
        if ok is not None:
            doc.setdefault("ok", ok)
        if outcome is not None:
            doc.setdefault("outcome", outcome)
        if result_status is not None:
            doc.setdefault("result_status", result_status)
        if executed is not None:
            doc.setdefault("executed", executed)
        if operation_id is not None:
            doc.setdefault("operation_id", operation_id)
        doc.setdefault("ok", "error" not in doc)
    else:
        doc = {}
        doc["ok"] = (
            ok
            if ok is not None
            else (outcome == "success" if outcome is not None else error is None)
        )
        if outcome is not None:
            doc["outcome"] = outcome
        if result_status is not None:
            doc["result_status"] = result_status
        if executed is not None:
            doc["executed"] = executed
        if operation_id:
            doc["operation_id"] = operation_id
        if error is not None:
            doc["error"] = error
        doc["output"] = body
    doc["cap_bytes"] = max_bytes
    doc["cap_tokens"] = max_tokens
    doc.setdefault("truncated", False)
    doc["payload_bytes"] = 0
    doc["payload_tokens"] = 0
    content_keys = [
        key
        for key, value in doc.items()
        if not _is_protected_key(key) and value not in (None, "", [], {})
    ]
    # Representability gate measured at final accounting width: the
    # protected contract plus the minimal honest ``output`` must fit.
    # An unrepresentable contract refuses — an ``error`` envelope that
    # fits, or ValueError when even the refusal exceeds the cap.
    if not _fits_emitted(
        _mandatory_floor(doc), max_bytes, max_tokens, worst_counters=True
    ):
        return _emit_doc(_refusal_doc(doc), max_bytes, max_tokens)
    for _ in range(12):
        raw = json.dumps(doc, ensure_ascii=False)
        size = len(raw.encode("utf-8"))
        tokens = count_tokens(raw).tokens
        converged = (doc["payload_bytes"], doc["payload_tokens"]) == (
            size,
            tokens,
        )
        doc["payload_bytes"] = size
        doc["payload_tokens"] = tokens
        if size <= max_bytes and tokens <= max_tokens:
            if not converged:
                continue
            if _content_lost(doc, content_keys):
                return _emit_doc(
                    _refusal_doc(doc), max_bytes, max_tokens
                )
            return raw
        try:
            doc = shrink_json_payload(
                doc, max_bytes=max_bytes, max_tokens=max_tokens
            )
        except ValueError:
            return _emit_doc(_refusal_doc(doc), max_bytes, max_tokens)
    # Iterations exhausted — emit the verified floor; when no content
    # survived at all the envelope is a refusal, never a silent loss.
    doc = _mandatory_floor(doc)
    if _content_lost(doc, content_keys):
        doc = _refusal_doc(doc)
    return _emit_doc(doc, max_bytes, max_tokens)
