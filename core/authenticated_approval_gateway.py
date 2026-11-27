"""Authentication-bound facade for owner approval requests.

Session tokens are obtained only from trusted application composition (for
example, an OS keyring-backed desktop session), never from model arguments.
"""
from __future__ import annotations

from typing import Any, Callable

from core.approval_flow import ApprovalCoordinator, ApprovalOutcome
from core.auth import BuiltInAuthService


class AuthenticatedApprovalGateway:
    """Bind approval creation and confirmation to a live authenticated session."""

    def __init__(
        self,
        auth_service: BuiltInAuthService,
        coordinator: ApprovalCoordinator,
        *,
        session_token_provider: Callable[[], str | None],
    ):
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be BuiltInAuthService")
        if not isinstance(coordinator, ApprovalCoordinator):
            raise TypeError("coordinator must be ApprovalCoordinator")
        if not callable(session_token_provider):
            raise TypeError("session_token_provider must be callable")
        self._auth = auth_service
        self._coordinator = coordinator
        self._token_provider = session_token_provider

    def _principal(self):
        token = self._token_provider()
        if not isinstance(token, str) or not token.strip() or len(token) > 256:
            return None
        user = self._auth.resolve_session(token)
        if user is None:
            return None
        return user, token

    def request(self, planner: Any, proposed_result: Any) -> ApprovalOutcome:
        """Persist and audit a proposal; does not execute it."""
        try:
            principal = self._principal()
        except Exception:
            principal = None
        if principal is None:
            return ApprovalOutcome(False, "authentication_required")
        user, session_token = principal
        if "workflow:execute" not in user.permissions:
            return ApprovalOutcome(False, "permission_denied")
        try:
            return self._coordinator.record(
                planner, proposed_result, actor=user.user_id,
                session_id=session_token,
            )
        except Exception:
            # Do not return exception text that might contain action arguments.
            return ApprovalOutcome(False, "approval_request_failed")

    def confirm(
        self,
        planner: Any,
        pending_id: str,
        *,
        confirm: Callable[[dict], bool],
    ) -> ApprovalOutcome:
        """Re-authenticate, verify approval permission, then invoke coordinator."""
        if not callable(confirm):
            return ApprovalOutcome(False, "confirmation_unavailable")
        try:
            principal = self._principal()
        except Exception:
            principal = None
        if principal is None:
            return ApprovalOutcome(False, "authentication_required")
        user, session_token = principal
        if "workflow:approve" not in user.permissions:
            return ApprovalOutcome(False, "permission_denied")
        try:
            return self._coordinator.approve(
                planner, pending_id, actor=user.user_id,
                session_id=session_token, confirm=confirm,
            )
        except Exception:
            return ApprovalOutcome(False, "approval_execution_failed")
