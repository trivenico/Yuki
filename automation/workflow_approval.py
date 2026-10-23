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
    def approve(self, run_id: str, *, request_context: Any, expected_version: int) -> WorkflowRun:
        principal = self.principal_resolver(request_context)
        if not isinstance(principal, AuthenticatedPrincipal):
            raise PermissionError("No trusted principal was resolved.")
        if not principal.actor_id.strip() or not principal.session_id.strip():
            raise PermissionError("Authenticated principal is incomplete.")
        if self.APPROVE_PERMISSION not in principal.permissions:
            raise PermissionError("Missing workflow approval permission.")
        run = self.store.get(run_id)
        if run is None:
            raise KeyError("Workflow run not found.")
        if not self.store._requires_approval(run.definition):
            raise ValueError("Workflow does not require approval.")
        if self.authorize is not None:
            try:
                allowed = self.authorize(principal, run) is True
            except Exception as exc:
                raise PermissionError("Authorization failed closed.") from exc
            if not allowed:
                raise PermissionError("Principal cannot approve this workflow.")
        return self.store.approve(run_id, actor_id=principal.actor_id, session_id=principal.session_id, expected_version=expected_version)
