"""Authenticated approval inbox backed by trusted durable task state.

Requesters identify a task, never a PolicyRequest. The service reconstructs the
policy request from the persisted task, its authenticated goal owner, and the
server-side registered skill metadata.
"""
from __future__ import annotations

from typing import Any, Callable
import re

from core.auth import AuthenticationError
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import PolicyRequest, Risk, argument_digest


class AuthenticatedCapabilityApprovalService:
    """Enforce authenticated ownership and approval permissions."""

    def __init__(
        self,
        auth: Any,
        gateway: CapabilityApprovalGateway,
        *,
        task_engine: Any,
        skill_registry: Any,
        deliver_credential: Callable[[str, str], None] | None = None,
        approval_worker_id: str | None = None,
    ) -> None:
        if not callable(getattr(auth, "resolve_session", None)):
            raise TypeError("auth must provide resolve_session(token)")
        if not isinstance(gateway, CapabilityApprovalGateway):
            raise TypeError("gateway must be a CapabilityApprovalGateway")
        if not callable(getattr(task_engine, "get_task", None)) or not callable(getattr(task_engine, "get_goal", None)):
            raise TypeError("task_engine must provide get_task(task_id) and get_goal(goal_id)")
        if not callable(getattr(skill_registry, "get", None)):
            raise TypeError("skill_registry must provide get(skill_name)")
        if deliver_credential is not None and not callable(deliver_credential):
            raise TypeError("deliver_credential must be callable")
        inferred_worker_id = getattr(getattr(deliver_credential, "__self__", None), "worker_id", None)
        if inferred_worker_id is not None and (
            not isinstance(inferred_worker_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", inferred_worker_id)
        ):
            raise ValueError("credential broker exposes an invalid worker identity")
        if approval_worker_id is not None and inferred_worker_id is not None and approval_worker_id != inferred_worker_id:
            raise ValueError("approval_worker_id must match the credential broker worker identity")
        if approval_worker_id is None:
            approval_worker_id = inferred_worker_id
        if approval_worker_id is not None and (
            not isinstance(approval_worker_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", approval_worker_id)
        ):
            raise ValueError("approval_worker_id must be a bounded trusted worker identity")
        self.auth = auth
        self.gateway = gateway
        self.task_engine = task_engine
        self.skill_registry = skill_registry
        self.deliver_credential = deliver_credential
        self.approval_worker_id = approval_worker_id

    def _principal(self, session_token: str, permission: str | None = None):
        user = self.auth.resolve_session(session_token)
        if user is None:
            raise AuthenticationError("Authentication required.")
        if permission is not None:
            permissions = getattr(user, "permissions", ())
            if permission not in permissions:
                raise PermissionError("The authenticated user lacks the required permission.")
        user_id = getattr(user, "user_id", None)
        if not isinstance(user_id, str) or not user_id.strip():
            raise AuthenticationError("Authenticated principal is invalid.")
        return user

    def _request_from_owned_task(self, user_id: str, task_id: str) -> PolicyRequest:
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            raise ValueError("task_id must be non-empty bounded text")
        task = self.task_engine.get_task(task_id)
        if task.get("owner_id") != user_id:
            raise PermissionError("approval requests must belong to the authenticated task owner")
        goal_id = task.get("goal_id")
        if not isinstance(goal_id, str) or not goal_id:
            raise PermissionError("task has no valid goal provenance")
        goal = self.task_engine.get_goal(goal_id)
        if goal.get("owner_id") != user_id or goal.get("state") != "active":
            raise PermissionError("task goal is not owned by the authenticated principal")
        if task.get("state") != "queued":
            raise PermissionError("only queued tasks can request approval")
        if task.get("task_type") != "skill.invoke" or task.get("max_attempts") != 1:
            raise PermissionError("task is not an eligible single-attempt skill invocation")
        payload = task.get("payload")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"skill_name", "argument"}
            or not isinstance(payload.get("skill_name"), str)
            or not payload["skill_name"]
            or len(payload["skill_name"]) > 128
            or not isinstance(payload.get("argument"), str)
            or len(payload["argument"]) > 20000
        ):
            raise ValueError("persisted skill task payload is invalid")
        skill_name = payload["skill_name"]
        skill = self.skill_registry.get(skill_name)
        if skill is None or getattr(skill, "name", None) != skill_name:
            raise PermissionError("task does not reference a trusted registered skill")
        raw_risk = getattr(skill, "risk", None)
        if not isinstance(raw_risk, str):
            raise PermissionError("registered skill has no trusted risk classification")
        try:
            risk = Risk(raw_risk.lower())
        except ValueError:
            raise PermissionError("registered skill risk classification is invalid") from None
        return PolicyRequest(
            principal_id=user_id,
            trigger="durable_task",
            capability="skill.invoke",
            target=skill_name,
            argument_sha256=argument_digest({"argument": payload["argument"]}),
            risk=risk,
            run_id=goal_id,
            step_id=task_id,
        )

    def request_approval(self, session_token: str, task_id: str, *, ttl_seconds: int = 86400) -> str:
        """Request review for a real queued task owned by the current session."""
        user = self._principal(session_token)
        request = self._request_from_owned_task(user.user_id, task_id)
        request_id = self.gateway._request_digest(request)[:32]
        self.task_engine.hold_task_for_approval(
            task_id, user.user_id, request_id, ttl_seconds=ttl_seconds,
        )
        try:
            persisted_id = self.gateway.request_approval(
                request, ttl_seconds=ttl_seconds, request_id=request_id,
            )
            if persisted_id != request_id:
                raise RuntimeError("approval request identifier mismatch")
            return persisted_id
        except Exception:
            self.task_engine.release_task_approval_hold(task_id, request_id)
            raise

    def pending_requests(self, session_token: str, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        """List redacted pending approvals for an authorized approver."""
        self._principal(session_token, "workflow:approve")
        return self.gateway.pending_requests(limit=limit)

    def approve_pending(self, session_token: str, request_id: str, *, ttl_seconds: int = 300) -> None:
        """Approve an inbox item and deliver its token only to a trusted resolver."""
        user = self._principal(session_token, "workflow:approve")
        if self.deliver_credential is None:
            raise RuntimeError("protected credential delivery is not configured")
        # Rebuild from authoritative task state at decision time. A task may
        # have been claimed, cancelled, modified, or had its goal paused after
        # the inbox item was created.
        identity = self.gateway.pending_request_identity(request_id)
        current_request = self._request_from_owned_task(
            identity["principal_id"], identity["step_id"],
        )
        self.task_engine.renew_task_approval_hold(
            identity["step_id"], request_id, ttl_seconds=ttl_seconds,
        )
        token = self.gateway.approve_request(
            request_id, approved_by=user.user_id, ttl_seconds=ttl_seconds,
            expected_request=current_request,
        )
        try:
            self.deliver_credential(request_id, token)
            if self.approval_worker_id is not None:
                self.task_engine.bind_task_worker(
                    identity["step_id"], request_id, self.approval_worker_id,
                )
        except Exception:
            try:
                self.gateway.revoke(token, revoked_by=user.user_id)
            except Exception:
                pass
            broker = getattr(self.deliver_credential, "__self__", None)
            revoke_delivery = getattr(broker, "revoke", None)
            if callable(revoke_delivery):
                try:
                    revoke_delivery(request_id)
                except Exception:
                    pass
            self.task_engine.release_task_approval_hold(identity["step_id"], request_id)
            raise RuntimeError("protected approval credential delivery failed") from None
        self.task_engine.release_task_approval_hold(identity["step_id"], request_id)

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
