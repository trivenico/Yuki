"""Production security composition primitives shared by Yuki entry points.

This module deliberately requires explicit principal permissions and persists every
central authorization decision before a protected operation can proceed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe

from core.authorization import AuthorizationDecision, AuthorizationRequest
from core.authorization_gateway import AuthorizationGateway


def require_supported_entrypoint(entrypoint: str) -> None:
    """Block legacy entry points when deployment requires central authorization."""
    if not isinstance(entrypoint, str) or not entrypoint.strip() or len(entrypoint) > 128:
        raise ValueError("entrypoint must be non-empty bounded text")
    required = os.environ.get("YUKI_REQUIRE_CENTRAL_AUTHORIZATION", "").strip().lower()
    if required in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "central_authorization_not_composed_for_entrypoint:" + entrypoint
        )


class PrincipalPermissionPolicy:
    """Small explicit-permission policy; callers must resolve trusted principals."""

    def __init__(self, policy_revision: str):
        if not isinstance(policy_revision, str) or not policy_revision.strip() or len(policy_revision) > 128:
            raise ValueError("policy_revision must be non-empty text of at most 128 characters")
        self.policy_revision = policy_revision.strip()

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        allowed = request.action in request.principal.permissions
        return AuthorizationDecision(
            allowed=allowed,
            decision_id=token_urlsafe(18),
            policy_revision=self.policy_revision,
            reason_code="permission_granted" if allowed else "permission_missing",
        )


class SQLiteAuthorizationAuditLog:
    """Append-only, hash-chained authorization audit log.

    Each decision is committed under BEGIN IMMEDIATE. The event stores only the
    authorization resource identifier supplied by the enforcement point; provider
    adapters use a digest there instead of raw prompts or tool arguments.
    """

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if not self.verify_chain():
            raise RuntimeError("authorization_audit_integrity_check_failed")

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS authorization_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    principal_id TEXT NOT NULL,
                    principal_kind TEXT NOT NULL,
                    tenant_id TEXT,
                    agent_id TEXT,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    task_id TEXT,
                    delegation_id TEXT,
                    approval_id TEXT,
                    expected_policy_revision TEXT,
                    decision_policy_revision TEXT NOT NULL,
                    allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                )
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS authorization_audit_no_update
                BEFORE UPDATE ON authorization_audit_events
                BEGIN SELECT RAISE(ABORT, 'authorization audit is append-only'); END
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS authorization_audit_no_delete
                BEFORE DELETE ON authorization_audit_events
                BEGIN SELECT RAISE(ABORT, 'authorization audit is append-only'); END
            """)
            connection.commit()
            if os.name == "posix":
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    raise
        finally:
            connection.close()

    def __call__(self, request: AuthorizationRequest, decision: AuthorizationDecision) -> None:
        if not isinstance(request, AuthorizationRequest) or not isinstance(decision, AuthorizationDecision):
            raise TypeError("audit requires typed authorization request and decision")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT event_hash FROM authorization_audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous[0] if previous else "0" * 64
            event = {
                "decision_id": decision.decision_id,
                "principal_id": request.principal.principal_id,
                "principal_kind": request.principal.kind,
                "tenant_id": request.principal.tenant_id,
                "agent_id": request.principal.agent_id,
                "action": request.action,
                "resource": request.resource,
                "risk": request.risk,
                "task_id": request.task_id,
                "delegation_id": request.delegation_id,
                "approval_id": request.approval_id,
                "expected_policy_revision": request.expected_policy_revision,
                "decision_policy_revision": decision.policy_revision,
                "allowed": decision.allowed,
                "reason_code": decision.reason_code,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "previous_hash": previous_hash,
            }
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            event_hash = hashlib.sha256(canonical).hexdigest()
            connection.execute("""
                INSERT INTO authorization_audit_events (
                    decision_id, principal_id, principal_kind, tenant_id, agent_id,
                    action, resource, risk, task_id, delegation_id, approval_id,
                    expected_policy_revision, decision_policy_revision, allowed,
                    reason_code, created_at, previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision.decision_id, request.principal.principal_id,
                request.principal.kind, request.principal.tenant_id,
                request.principal.agent_id, request.action, request.resource,
                request.risk, request.task_id, request.delegation_id,
                request.approval_id, request.expected_policy_revision,
                decision.policy_revision, int(decision.allowed), decision.reason_code,
                event["created_at"], previous_hash, event_hash,
            ))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def verify_chain(self) -> bool:
        connection = self._connect()
        try:
            rows = connection.execute("""
                SELECT decision_id, principal_id, principal_kind, tenant_id, agent_id,
                       action, resource, risk, task_id, delegation_id, approval_id,
                       expected_policy_revision, decision_policy_revision, allowed,
                       reason_code, created_at, previous_hash, event_hash
                FROM authorization_audit_events ORDER BY sequence
            """).fetchall()
        finally:
            connection.close()
        previous_hash = "0" * 64
        for row in rows:
            keys = (
                "decision_id", "principal_id", "principal_kind", "tenant_id", "agent_id",
                "action", "resource", "risk", "task_id", "delegation_id", "approval_id",
                "expected_policy_revision", "decision_policy_revision", "allowed",
                "reason_code", "created_at", "previous_hash",
            )
            event = dict(zip(keys, row[:-1]))
            event["allowed"] = bool(event["allowed"])
            if event["previous_hash"] != previous_hash:
                return False
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            actual = hashlib.sha256(canonical).hexdigest()
            if actual != row[-1]:
                return False
            previous_hash = actual
        return True


@dataclass(frozen=True)
class AuthorizationSecurityRuntime:
    gateway: AuthorizationGateway
    policy: PrincipalPermissionPolicy
    audit_log: SQLiteAuthorizationAuditLog


def build_authorization_security_runtime(
    database_path: str | Path,
    *,
    policy_revision: str,
) -> AuthorizationSecurityRuntime:
    """Build one shared policy/gateway/audit triple for an application process."""
    policy = PrincipalPermissionPolicy(policy_revision)
    audit_log = SQLiteAuthorizationAuditLog(database_path)
    return AuthorizationSecurityRuntime(
        gateway=AuthorizationGateway(policy, audit=audit_log),
        policy=policy,
        audit_log=audit_log,
    )
