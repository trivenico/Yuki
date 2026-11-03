"""Human-in-the-loop approval. Fail closed: no callback means no approval."""
from dataclasses import dataclass
import secrets
from typing import Callable, Optional


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    reason: str
    risk: str = "low"


class PermissionGate:
    def __init__(
        self,
        approver: Optional[Callable[[ApprovalRequest], bool]] = None,
        audit_log=None,
        *,
        actor_id: str = "local-cli",
        session_id: str | None = None,
        actor_role: str = "operator",
    ):
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise ValueError("actor_id must be a non-empty bounded string.")
        if session_id is None:
            session_id = secrets.token_urlsafe(24)
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
            raise ValueError("session_id must be a non-empty bounded string.")
        if actor_role not in {"operator", "admin"}:
            raise ValueError("actor_role must be operator or admin.")
        self._approver = approver
        self._audit_log = audit_log
        self._actor_id = actor_id
        self._session_id = session_id
        self._actor_role = actor_role

    @classmethod
    def from_authenticated_identity(cls, identity, approver, audit_log=None):
        """Build a request-scoped gate from a server-verified identity.

        End-user roles deliberately cannot approve privileged operations.
        """
        from core.identity import AuthenticatedIdentity
        if not isinstance(identity, AuthenticatedIdentity):
            raise TypeError("identity must be a server-verified AuthenticatedIdentity.")
        role = identity.role
        if role not in {"admin", "operator"}:
            raise PermissionError("This authenticated role cannot approve actions.")
        actor_id = identity.actor_id
        session_id = identity.session_id
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("Verified actor identity is required.")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("Verified session identity is required.")
        return cls(
            approver, audit_log, actor_id=actor_id,
            session_id=session_id, actor_role=role,
        )

    @property
    def actor_id(self) -> str:
        return self._actor_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_admin(self) -> bool:
        """True only when the trusted application boundary assigned the admin role."""
        return self._actor_role == "admin"

    def confirm_durable_action(self, *, action: str, reason: str, risk: str = "low") -> bool:
        """Ask the configured human approver without writing a parallel audit trail.

        ApprovalCoordinator owns the durable requested/consumed/approved/terminal
        events when this method is used.
        """
        if self._approver is None:
            return False
        if not isinstance(action, str) or not action.strip():
            return False
        if not isinstance(reason, str) or not reason.strip():
            return False
        if risk not in {"low", "medium", "high"}:
            return False
        try:
            return self._approver(
                ApprovalRequest(action=action, reason=reason, risk=risk)
            ) is True
        except Exception:
            return False

    def _audit(self, event_type: str, receipt_id: str, skill_id: str) -> bool:
        """Write metadata only; a configured but unavailable audit fails closed."""
        if self._audit_log is None:
            return True
        try:
            self._audit_log.append(
                event_type=event_type,
                pending_id=receipt_id,
                actor=self._actor_id,
                session_id=self._session_id,
                skill_id=skill_id,
            )
            return True
        except Exception:
            return False

    def request(self, action: str, reason: str, risk: str = "low") -> bool:
        approved, _receipt_id = self.request_with_receipt(action, reason, risk)
        return approved

    def request_with_receipt(
        self, action: str, reason: str, risk: str = "low", *, skill_id: str = "permission_gate"
    ) -> tuple[bool, str | None]:
        """Authorize one action and return a unique correlation receipt for its audit trail."""
        if not isinstance(action, str) or not action.strip():
            return False, None
        if not isinstance(reason, str) or not reason.strip():
            return False, None
        if not isinstance(risk, str) or risk not in {"low", "medium", "high"}:
            return False, None
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            return False, None
        if self._approver is None:
            return False, None
        receipt_id = secrets.token_urlsafe(24)
        if not self._audit("requested", receipt_id, skill_id):
            return False, None
        try:
            approved = self._approver(ApprovalRequest(action=action, reason=reason, risk=risk)) is True
        except Exception:
            approved = False
        if not self._audit("approved" if approved else "denied", receipt_id, skill_id):
            return False, None
        return approved, receipt_id if approved else None

    def record_execution(self, receipt_id: str, *, skill_id: str, succeeded: bool) -> bool:
        """Append the terminal execution event for an approved runtime receipt."""
        if not isinstance(receipt_id, str) or not receipt_id.strip() or len(receipt_id) > 128:
            return False
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            return False
        if type(succeeded) is not bool:
            return False
        return self._audit("executed" if succeeded else "execution_failed", receipt_id, skill_id)
