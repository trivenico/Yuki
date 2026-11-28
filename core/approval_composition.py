"""Trusted composition root for the durable guarded-approval subsystem.

This factory deliberately does not attach itself to a host or enable handlers.
A host must opt in explicitly and supply its trusted session-token provider and
the exact handler/scope registry it is prepared to expose.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Any

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.auth import BuiltInAuthService, guarded_runtime_identity_validator
from core.authenticated_approval_gateway import AuthenticatedApprovalGateway
from core.guarded_approval_executor import GuardedApprovalExecutor
from core.maintenance_fence import MaintenanceFence
from core.pending_approvals import SQLitePendingApprovalStore
from core.tool_action_approvals import SQLiteToolApprovalStore


class ApprovalCompositionError(RuntimeError):
    """The approval subsystem could not be composed safely."""


@dataclass(frozen=True)
class ApprovalServices:
    """All durable approval services sharing one profile and fence."""

    pending: SQLitePendingApprovalStore
    audit: SQLiteApprovalAuditLog
    grants: SQLiteToolApprovalStore
    executor: GuardedApprovalExecutor
    coordinator: ApprovalCoordinator
    gateway: AuthenticatedApprovalGateway
    maintenance_fence: MaintenanceFence
    profile_root: Path


def build_approval_services(
    *,
    auth_service: BuiltInAuthService,
    profile_root: str | Path,
    maintenance_fence: MaintenanceFence,
    session_token_provider: Callable[[], str | None],
    handlers: Mapping[str, Callable[[str], Any]],
    target_scopes: Mapping[str, str],
    pending_ttl_seconds: int = 300,
    grant_ttl_seconds: int = 300,
    max_pending: int = 128,
) -> ApprovalServices:
    """Build the complete approval graph without registering it with a host.

    Authentication must already be constructed by the host's canonical
    control-plane factory. Its database must be directly beneath profile_root
    and its maintenance_fence must be this exact object. No path or fence
    overrides are accepted for individual approval stores.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be BuiltInAuthService")
    if not isinstance(maintenance_fence, MaintenanceFence):
        raise TypeError("a trusted MaintenanceFence is required")
    if auth_service.maintenance_fence is not maintenance_fence:
        raise ApprovalCompositionError("authentication and approvals must share one maintenance fence")
    if not callable(session_token_provider):
        raise TypeError("trusted session_token_provider is required")
    if not isinstance(handlers, Mapping) or not handlers:
        raise ValueError("an explicit non-empty handler registry is required")
    if not isinstance(target_scopes, Mapping) or set(handlers) != set(target_scopes):
        raise ValueError("every handler must have exactly one target scope")

    root = Path(profile_root).expanduser().resolve(strict=True)
    fence_root = maintenance_fence.profile_root.expanduser().resolve(strict=True)
    auth_path = Path(auth_service.database_path).expanduser().resolve(strict=True)
    if root != fence_root or auth_path.parent != root:
        raise ApprovalCompositionError("profile root, auth database, and maintenance fence do not match")
    if auth_path.is_symlink() or not auth_path.is_file():
        raise ApprovalCompositionError("existing regular authentication database is required")

    try:
        pending = SQLitePendingApprovalStore(
            str(root / "pending-approvals.sqlite3"),
            ttl_seconds=pending_ttl_seconds,
            max_pending=max_pending,
            maintenance_fence=maintenance_fence,
        )
        audit = SQLiteApprovalAuditLog(
            str(root / "approval-audit.sqlite3"),
            maintenance_fence=maintenance_fence,
        )
        grants = SQLiteToolApprovalStore(
            str(root / "tool-approvals.sqlite3"),
            ttl_seconds=grant_ttl_seconds,
            max_pending=max_pending,
            maintenance_fence=maintenance_fence,
        )
        identity_validator = guarded_runtime_identity_validator(
            auth_service, session_token_provider=session_token_provider,
        )
        executor = GuardedApprovalExecutor(
            handlers,
            approval_store=grants,
            audit_log=audit,
            identity_validator=identity_validator,
            target_scopes=target_scopes,
        )
        coordinator = ApprovalCoordinator(
            pending_store=pending,
            audit_log=audit,
            guarded_executor=executor,
            max_pending=max_pending,
            maintenance_fence=maintenance_fence,
        )
        gateway = AuthenticatedApprovalGateway(
            auth_service, coordinator,
            session_token_provider=session_token_provider,
        )
    except Exception as exc:
        raise ApprovalCompositionError(
            "The durable approval subsystem could not be composed safely."
        ) from exc

    stores = (pending, audit, grants)
    if any(store.maintenance_fence is not maintenance_fence for store in stores):
        raise ApprovalCompositionError("approval stores do not share the trusted maintenance fence")
    if any(Path(store.database_path).resolve(strict=True).parent != root for store in stores):
        raise ApprovalCompositionError("approval database escaped the private profile")

    return ApprovalServices(
        pending=pending,
        audit=audit,
        grants=grants,
        executor=executor,
        coordinator=coordinator,
        gateway=gateway,
        maintenance_fence=maintenance_fence,
        profile_root=root,
    )
