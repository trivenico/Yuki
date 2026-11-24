"""Explicit, bounded maintenance cycle for authorization audit checkpoints.

A deployment scheduler calls run_once(); this module creates no background
threads, timers, or implicit network activity.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.authorization_audit import (
    AuthorizationAuditAnchor,
    AuthorizationAuditCheckpoint,
    SQLiteAuthorizationAuditLog,
)


@dataclass(frozen=True)
class AuthorizationAuditMaintenanceReport:
    checkpoint: AuthorizationAuditCheckpoint
    published: bool
    verified: bool


class AuthorizationAuditMaintenance:
    """Verify the current anchor, publish a newer local head, and verify again."""

    def __init__(
        self,
        audit_log: SQLiteAuthorizationAuditLog,
        anchor: AuthorizationAuditAnchor,
    ) -> None:
        if not isinstance(audit_log, SQLiteAuthorizationAuditLog):
            raise TypeError("durable_authorization_audit_log_required")
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        self.audit_log = audit_log
        self.anchor = anchor

    def run_once(self) -> AuthorizationAuditMaintenanceReport:
        """Run one bounded maintenance cycle; any integrity/backend error fails closed."""
        if self.audit_log.verify_chain() is not True:
            raise RuntimeError("authorization_audit_local_chain_invalid")

        try:
            current = self.anchor.latest()
        except Exception as exc:
            raise RuntimeError("authorization_audit_anchor_unavailable") from exc
        if not isinstance(current, AuthorizationAuditCheckpoint):
            # Initial anchor provisioning is a separate operator-controlled action.
            raise RuntimeError("authorization_audit_anchor_not_provisioned")
        if not self.audit_log.verify_anchor(self.anchor):
            raise RuntimeError("authorization_audit_anchor_mismatch")

        local = self.audit_log.checkpoint()
        published = local != current
        if published:
            try:
                self.anchor.publish(local)
            except Exception as exc:
                raise RuntimeError("authorization_audit_checkpoint_publish_failed") from exc

        if not self.audit_log.verify_anchor(self.anchor):
            raise RuntimeError("authorization_audit_checkpoint_verification_failed")
        return AuthorizationAuditMaintenanceReport(
            checkpoint=local,
            published=published,
            verified=True,
        )
