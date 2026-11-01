from dataclasses import dataclass
from typing import Any, Callable
from automation.workflow_store import WorkflowRun, WorkflowRunStore

@dataclass(frozen=True)
class AuthenticatedPrincipal:
    actor_id: str
    session_id: str
    permissions: frozenset[str]

class WorkflowApprovalService:
    APPROVE_PERMISSION = "workflow:approve"
    def __init__(self, store: WorkflowRunStore, *, principal_resolver: Callable[[Any], AuthenticatedPrincipal], authorize: Callable[[AuthenticatedPrincipal, WorkflowRun], bool] | None = None):
        if not callable(principal_resolver) or (authorize is not None and not callable(authorize)):
            raise ValueError("Resolver and optional authorizer must be callable.")
        self.store, self.principal_resolver, self.authorize = store, principal_resolver, authorize
    def approval_requirements(self, run_id: str, *, request_context: Any) -> dict[str, Any]:
        """Return a redacted permission summary before an operator approves a run."""
        principal = self.principal_resolver(request_context)
        if not isinstance(principal, AuthenticatedPrincipal):
            raise PermissionError("No trusted principal was resolved.")
        if (
            not isinstance(principal.actor_id, str) or not principal.actor_id.strip()
            or not isinstance(principal.session_id, str) or not principal.session_id.strip()
            or not isinstance(principal.permissions, frozenset)
        ):
            raise PermissionError("Authenticated principal is incomplete.")
        if not ({"workflow:read", self.APPROVE_PERMISSION} & principal.permissions):
            raise PermissionError("Missing workflow read or approval permission.")
        run = self.store.get(run_id)
        if run is None:
            raise KeyError("Workflow run not found.")
        if self.authorize is not None:
            try:
                allowed = self.authorize(principal, run) is True
            except Exception as exc:
                raise PermissionError("Authorization failed closed.") from exc
            if not allowed:
                raise PermissionError("Principal cannot inspect this workflow.")
        return {
            "run_id": run.run_id,
            "state": run.state,
            "version": run.version,
            "steps": [
                {
                    "name": step["name"],
                    "action": step["action"],
                    "requires_approval": step["requires_approval"],
                    "required_permissions": tuple(step.get("required_permissions", ())),
                }
                for step in run.definition
            ],
        }

    def approve(self, run_id: str, *, request_context: Any, expected_version: int) -> WorkflowRun:
        principal = self.principal_resolver(request_context)
        if not isinstance(principal, AuthenticatedPrincipal):
            raise PermissionError("No trusted principal was resolved.")
        if (
            not isinstance(principal.actor_id, str) or not principal.actor_id.strip()
            or not isinstance(principal.session_id, str) or not principal.session_id.strip()
            or not isinstance(principal.permissions, frozenset)
        ):
            raise PermissionError("Authenticated principal is incomplete.")
        if self.APPROVE_PERMISSION not in principal.permissions:
            raise PermissionError("Missing workflow approval permission.")
        run = self.store.get(run_id)
        if run is None:
            raise KeyError("Workflow run not found.")
        if not any(step.get("requires_approval", True) is True for step in run.definition):
            raise ValueError("Workflow does not require approval.")
        if self.authorize is not None:
            try:
                allowed = self.authorize(principal, run) is True
            except Exception as exc:
                raise PermissionError("Authorization failed closed.") from exc
            if not allowed:
                raise PermissionError("Principal cannot approve this workflow.")
        return self.store.approve(run_id, actor_id=principal.actor_id, session_id=principal.session_id, expected_version=expected_version)
