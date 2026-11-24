"""Explicit local-only authorization composition for the interactive CLI.

This is not a production trust anchor or hosted identity provider. Production
services must use create_production_security_runtime with PostgreSQL and an
independently administered audit anchor.
"""
from __future__ import annotations

from pathlib import Path

from core.authorization import AuthorizationDecision, Principal
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.authorization_gateway import AuthorizationGateway
from core.production_security import ProductionSecurityContext

LOCAL_OPERATOR = "local-operator"
LOCAL_WORKER = "yuki-local-worker"

_OPERATOR_ACTIONS = frozenset({
    "task:create", "task:list", "task:read", "task:approve",
    "task:cancel", "task:retry", "task:recover",
    "agent:read", "agent:plan", "agent:dispatch", "agent:cancel",
    "agent:delegate",
})
_WORKER_ACTIONS = frozenset({
    "agent:worker:claim", "agent:invoke", "task:progress",
    "task:lease:renew", "task:artifact", "task:complete", "task:fail",
})


class _LocalAuditAnchor:
    """Process-local checkpoint; intentionally unsuitable for production."""

    def __init__(self):
        self._checkpoint = None

    def publish(self, checkpoint) -> None:
        self._checkpoint = checkpoint

    def latest(self):
        return self._checkpoint


class _LocalOperatorPolicy:
    def authorize(self, request):
        principal = request.principal
        allowed = (
            principal.kind == "human"
            and principal.principal_id == LOCAL_OPERATOR
            and request.action in _OPERATOR_ACTIONS
        ) or (
            principal.kind == "workload"
            and principal.principal_id == LOCAL_WORKER
            and request.action in _WORKER_ACTIONS
        )
        return AuthorizationDecision(
            allowed,
            "local-cli-policy",
            "local-cli-v1",
            "allowed" if allowed else "local_policy_denied",
        )


def build_local_cli_security_context(workspace: str | Path) -> ProductionSecurityContext:
    """Build a narrowly scoped, local CLI-only context with a durable SQLite log."""
    root = Path(workspace).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    audit = SQLiteAuthorizationAuditLog(str(root / "local-authorization-audit.sqlite3"))
    anchor = _LocalAuditAnchor()
    audit.publish_checkpoint(anchor)

    operator = Principal(
        LOCAL_OPERATOR, "human", tenant_id="local",
        permissions=_OPERATOR_ACTIONS,
    )
    worker = Principal(
        LOCAL_WORKER, "workload", tenant_id="local", agent_id="local-specialist",
        permissions=_WORKER_ACTIONS,
    )
    principals = {LOCAL_OPERATOR: operator, LOCAL_WORKER: worker}
    return ProductionSecurityContext(
        authorization_gateway=AuthorizationGateway(_LocalOperatorPolicy(), audit=audit),
        principal_for_actor=lambda actor_id: principals.get(actor_id),
        principal_for_worker=lambda worker_id: worker if worker_id == LOCAL_WORKER else None,
        policy_revision="local-cli-v1",
        audit_anchor=anchor,
    )
