"""Presentation for the capability surface — list table, detail, invoke result.

Pure formatting over ``Capability``/``CapabilityView``/``InvocationResult``;
JSON output comes from the dataclasses' own ``to_dict``.
"""

from __future__ import annotations

import shlex

from greedy_token.capabilities import Capability, CapabilityView
from greedy_token.capabilities_invoke import InvocationResult


def format_capabilities(view: CapabilityView) -> str:
    lines = [
        "Deterministic operations — derived from routes + wrappers + trust + fs",
        f"root: {view.root}",
        (
            f"ops: {len(view.ops)} · invocable: "
            f"{sum(1 for op in view.ops if op.invocable)}"
        ),
    ]
    if view.manifest_error:
        lines.append(f"trust manifest: UNREADABLE — {view.manifest_error}")
    lines.append("")
    width = max((len(op.id) for op in view.ops), default=10)
    for op in view.ops:
        flag = "invocable" if op.invocable else "—"
        params = f" params={','.join(op.params)}" if op.params else ""
        tail = op.reason if not op.invocable else (op.note or op.reason)
        lines.append(
            f"  {op.id:<{width}}  [{op.source}:{op.tier}]  "
            f"{op.readiness:<20} {flag:<9}  {tail}{params}"
        )
    lines.append("")
    lines.append("Inspect: greedy-token capabilities show <id> [--json]")
    lines.append("Invoke:  greedy-token capabilities invoke <id> [--query Q|--args A]")
    return "\n".join(lines)


def format_capability_detail(cap: Capability) -> str:
    lines = [
        f"{cap.id}  [{cap.source}/{cap.origin}:{cap.tier}]  {cap.readiness}",
        (
            f"  invocable: {'yes' if cap.invocable else 'no'}"
            f"  ·  read_only: {cap.read_only}  ·  status: {cap.status}"
        ),
        f"  reason:    {cap.reason}",
    ]
    if cap.command:
        lines.append(f"  command:   {cap.command}")
    if cap.argv:
        lines.append(f"  argv:      {shlex.join(list(cap.argv))}")
    if cap.params:
        lines.append(f"  params:    {', '.join(cap.params)}")
    else:
        lines.append("  params:    none — fixed argv")
    if cap.missing_paths:
        lines.append(
            f"  missing_paths: {', '.join(cap.missing_paths)} — declared but not on disk"
        )
    if cap.script_path:
        lines.append(f"  script:    {cap.script_path} ({cap.script_type or 'unknown'})")
    if cap.authorization:
        lines.append(f"  auth:      {cap.authorization}")
    if cap.trust_entry:
        lines.append(f"  trust:     {cap.trust_entry}")
    if cap.contract:
        lines.append(f"  contract:  {cap.contract}")
    if cap.lifecycle_state:
        lines.append(f"  lifecycle: {cap.lifecycle_state}")
    if cap.domains:
        lines.append(f"  domains:   {', '.join(cap.domains)}")
    if cap.patterns:
        lines.append(f"  patterns:  {', '.join(cap.patterns[:8])}")
    if cap.note:
        lines.append(f"  note:      {cap.note}")
    return "\n".join(lines)


def format_invocation_result(result: InvocationResult) -> str:
    if not result.invocable:
        head = (
            f"Refused: {result.op_id} [{result.refusal_code}] — {result.refusal_reason}"
        )
        return head
    lines = [result.output.rstrip()] if result.output else []
    if result.missing_paths:
        lines.append(
            "note: search_paths skipped (not on disk): "
            + ", ".join(result.missing_paths)
        )
    lines.extend(
        [
            "---",
            (
                f"invoke {result.op_id}: exit={result.exit_code} "
                f"executed={result.executed} result={result.result_status or 'n/a'} "
                f"gate={result.gate_action}/{result.gate_reason} "
                f"outcome={result.outcome} op={result.operation_id or '-'}"
            ),
        ]
    )
    return "\n".join(lines)
