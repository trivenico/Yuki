"""Read-only diagnostics for approval persistence recovery.

This module never mutates stores, revokes grants, or dispatches actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any


@dataclass(frozen=True)
class ApprovalRecoveryReport:
    generated_at: str
    audit_chain_valid: bool
    complete: bool
    findings: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    @property
    def finding_count(self) -> int:
        return len(self.findings)


def scan_approval_recovery(
    *,
    pending_store,
    audit_log,
    grant_store,
    limit: int = 10000,
) -> ApprovalRecoveryReport:
    """Inspect approval stores without changing persistent state.

    The report contains hashes and bounded metadata only. A full audit history
    larger than the requested bound is explicitly marked incomplete.
    """
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("limit must be between 1 and 10000.")
    for label, store, method in (
        ("pending_store", pending_store, "list_recovery_metadata"),
        ("audit_log", audit_log, "list_events"),
        ("grant_store", grant_store, "list_recovery_metadata"),
    ):
        if not callable(getattr(store, method, None)):
            raise TypeError(label + " does not support read-only recovery diagnostics.")
    if not callable(getattr(audit_log, "verify_chain", None)):
        raise TypeError("audit_log must support verify_chain().")
    if not callable(getattr(audit_log, "list_unresolved_executions", None)):
        raise TypeError("audit_log must support list_unresolved_executions().")

    generated_at = datetime.now(timezone.utc).isoformat()
    pending = pending_store.list_recovery_metadata(limit=limit)
    grants = grant_store.list_recovery_metadata(limit=limit)
    chain_valid = audit_log.verify_chain()
    events = audit_log.list_events(limit=limit)
    warnings: list[str] = []
    findings: list[dict[str, Any]] = []

    if len(pending) == limit:
        warnings.append("Pending snapshot listing reached its limit; report may be incomplete.")
    if len(grants) == limit:
        warnings.append("Grant listing reached its limit; report may be incomplete.")
    if len(events) == limit:
        warnings.append("Audit event listing reached its limit; lifecycle analysis may be incomplete.")

    if not chain_valid:
        warnings.append("Audit hash chain is invalid; event correlation was skipped.")
        return ApprovalRecoveryReport(
            generated_at, False, False, tuple(findings), tuple(warnings),
        )

    # list_events is newest-first. Correlation is performed only on this
    # bounded window; the report says when that window may be truncated.
    chronological = list(reversed(events))
    known_requested = {
        event.get("pending_id_hash")
        for event in chronological
        if event.get("event_type") == "requested"
    }
    event_groups: dict[str, list[dict[str, Any]]] = {}
    for event in chronological:
        key = event.get("pending_id_hash")
        if isinstance(key, str):
            event_groups.setdefault(key, []).append(event)

    for item in pending:
        pending_hash = item.get("pending_id_hash")
        if not isinstance(pending_hash, str) or pending_hash not in known_requested:
            findings.append({
                "kind": "orphan_pending_snapshot",
                "severity": "warning",
                "pending_id_hash": pending_hash,
                "skill_id": item.get("skill_id"),
                "expires_at": item.get("expires_at"),
                "expired": item.get("expired"),
            })

    terminal = {
        "denied", "execution_blocked", "executed", "execution_failed",
        "outcome_unknown", "reconciled_executed", "reconciled_not_executed",
    }
    for pending_hash, group in event_groups.items():
        requested = any(event.get("event_type") == "requested" for event in group)
        approved_positions = [
            index for index, event in enumerate(group)
            if event.get("event_type") == "approved"
        ]
        started = any(event.get("event_type") == "execution_started" for event in group)
        has_terminal = any(event.get("event_type") in terminal for event in group)
        if requested and approved_positions and not started and not has_terminal:
            findings.append({
                "kind": "approved_without_dispatch_marker",
                "severity": "review",
                "pending_id_hash": pending_hash,
                "approved_event_id": group[approved_positions[-1]].get("event_id"),
            })

    try:
        unresolved = audit_log.list_unresolved_executions(limit=min(limit, 1000))
        for item in unresolved:
            findings.append({
                "kind": "execution_outcome_requires_reconciliation",
                "severity": "critical",
                "started_event_id": item.get("started_event_id"),
                "pending_id_hash": item.get("pending_id_hash"),
                "skill_id": item.get("skill_id"),
                "started_at": item.get("started_at"),
            })
        if len(unresolved) == min(limit, 1000):
            warnings.append("Unresolved execution listing reached its limit.")
    except RuntimeError as exc:
        warnings.append("Unresolved execution listing unavailable: " + type(exc).__name__)

    for grant in grants:
        if grant.get("expired") is True:
            findings.append({
                "kind": "expired_one_shot_grant",
                "severity": "warning",
                "approval_id_hash": grant.get("approval_id_hash"),
                "action_digest": grant.get("action_digest"),
                "expires_at": grant.get("expires_at"),
            })

    return ApprovalRecoveryReport(
        generated_at=generated_at,
        audit_chain_valid=True,
        complete=not warnings,
        findings=tuple(findings),
        warnings=tuple(warnings),
    )
