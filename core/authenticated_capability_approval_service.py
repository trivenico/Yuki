"""Authenticated facade for durable capability approvals.

Approver identity is resolved from a live authentication session. Callers cannot
supply an actor ID, and approval tokens are returned only to this trusted
service caller for immediate delivery to a protected credential resolver.
"""
from __future__ import annotations

from typing import Any, Callable

from core.auth import AuthenticationError
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import PolicyRequest


class AuthenticatedCapabilityApprovalService:
    """Enforce authenticated approval permissions around the approval gateway."""

    def __init__(
        self,
        auth: Any,
        gateway: CapabilityApprovalGateway,
        *,
        deliver_credential: Callable[[str, PolicyRequest, str, int], None] | None = None,
    ) -> None:
        if not callable(getattr(auth, "resolve_session", None)):
            raise TypeError("auth must provide resolve_session(token)")
        if not isinstance(gateway, CapabilityApprovalGateway):
            raise TypeError("gateway must be a CapabilityApprovalGateway")
        if deliver_credential is not None and not callable(deliver_credential):
            raise TypeError("deliver_credential must be callable")
        self.auth = auth
        self.gateway = gateway
        self.deliver_credential = deliver_credential

    def _principal(self, session_token: str, permission: str):
        user = self.auth.resolve_session(session_token)
        if user is None:
            raise AuthenticationError("Authentication required.")
        permissions = getattr(user, "permissions", ())
        if permission not in permissions:
            raise PermissionError("The authenticated user lacks the required permission.")
        user_id = getattr(user, "user_id", None)
        if not isinstance(user_id, str) or not user_id.strip():
            raise AuthenticationError("Authenticated principal is invalid.")
        return user

    def request_approval(self, session_token: str, request: PolicyRequest, *, ttl_seconds: int = 86400) -> str:
        """Create an inbox item for the authenticated task owner."""
        if not isinstance(request, PolicyRequest):
            raise TypeError("request must be a validated PolicyRequest")
        user = self._principal(session_token, "workflow:approve")
        if request.principal_id != user.user_id:
            raise PermissionError("approval requests must belong to the authenticated principal")
        return self.gateway.request_approval(request, ttl_seconds=ttl_seconds)

    def pending_requests(self, session_token: str, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        """List redacted pending approvals for an authorized approver."""
        self._principal(session_token, "workflow:approve")
        return self.gateway.pending_requests(limit=limit)

    def approve_pending(self, session_token: str, request_id: str, *, ttl_seconds: int = 300) -> None:
        """Approve an inbox item and deliver its token only to a trusted resolver."""
        user = self._principal(session_token, "workflow:approve")
        if self.deliver_credential is None:
            raise RuntimeError("protected credential delivery is not configured")
        request = self.gateway.get_pending_request(request_id)
        token = self.gateway.approve_request(
            request_id, approved_by=user.user_id, ttl_seconds=ttl_seconds,
        )
        try:
            self.deliver_credential(request_id, request, token, ttl_seconds)
        except Exception:
            # Revoke even if the delivery callback partially completed. Never
            # propagate callback text, which could accidentally contain secrets.
            try:
                self.gateway.revoke(token, revoked_by=user.user_id)
            except Exception:
                pass
            raise RuntimeError("protected approval credential delivery failed") from None

    def approve(
        self,
        session_token: str,
        request: PolicyRequest,
        *,
        ttl_seconds: int = 300,
    ) -> str:
        """Approve one exact operation and return its opaque one-time credential.

        The caller must deliver the returned credential directly to a trusted
        resolver; it must not put it in task payloads, model context, or logs.
        """
        if not isinstance(request, PolicyRequest):
            raise TypeError("request must be a validated PolicyRequest")
        user = self._principal(session_token, "workflow:approve")
        return self.gateway.issue_approval(
            request, approved_by=user.user_id, ttl_seconds=ttl_seconds,
        )

    def revoke(self, session_token: str, approval_token: str) -> bool:
        """Revoke an unconsumed credential using the authenticated actor."""
        user = self._principal(session_token, "workflow:approve")
        return self.gateway.revoke(approval_token, revoked_by=user.user_id)

    def audit_events(
        self, session_token: str, *, limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """Read redacted approval audit metadata for authorized users."""
        self._principal(session_token, "workflow:read")
        return self.gateway.audit_events(limit=limit)
