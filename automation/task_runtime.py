"""Unified task facade over Yuki's durable workflow journal.

This service records task state and progress; it does not execute arbitrary
code or automatically replay work after an expired worker lease.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from typing import Any, Callable

from automation.workflow_repository import REQUIRED_WORKFLOW_REPOSITORY_METHODS, WorkflowRepository
from automation.workflow_store import WorkflowConflict, WorkflowRun, WorkflowTransitionError
from core.workload_identity import WorkloadCredentialClaims, WorkloadCredentialVerifier
from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway
from core.delegation_authority import DelegationAuthorityProtocol

_EVENT_KINDS = frozenset({"task.created", "task.approved", "task.progress", "task.artifact", "task.completed", "task.failed", "task.cancelled", "task.interrupted"})
_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


@dataclass(frozen=True)
class VerifiedWorkloadContext:
    """Immutable identity and authorization claims from a verified workload credential.

    Instances are created only after signature, audience, task, lease, scope,
    delegation, and central authorization checks succeed.
    """
    issuer: str
    subject: str
    tenant_id: str
    owner_principal_id: str
    agent_id: str
    audience: str
    token_id: str
    task_id: str
    lease_generation: str
    scopes: frozenset[str]
    delegation_id: str | None
    expires_at: int
    authorized_action: str
    authorization_decision_id: str
    policy_revision: str
    delegation_fence: Callable[[Any], None] | None = None


@dataclass(frozen=True)
class TaskEvent:
    event_id: int
    task_id: str
    sequence: int
    kind: str
    actor_id: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class TaskSummary:
    """Redacted task metadata for authorized dashboard listings."""
    task_id: str
    name: str
    state: str
    version: int
    attempts: int
    created_at: str
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    name: str
    state: str
    steps: list[dict[str, Any]]
    result: dict[str, Any] | list[Any] | None
    error_code: str | None
    version: int
    attempts: int
    created_at: str
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None


class TaskRuntime:
    """Task-oriented API with idempotent enqueue and versioned progress events."""

    def __init__(self, store: WorkflowRepository, *, authorize: Callable[[str, str, str], bool] | None = None,
                 authorization_gateway: AuthorizationGateway | None = None,
                 principal_for_actor: Callable[[str], Principal] | None = None,
                 policy_revision: str | None = None,
                 workload_credential_verifier: WorkloadCredentialVerifier | None = None,
                 workload_audience: str | None = None, credential_clock: Callable[[], float] = time.time,
                 delegation_authority: DelegationAuthorityProtocol | None = None,
                 require_workload_delegation: bool = False):
        missing = [name for name in REQUIRED_WORKFLOW_REPOSITORY_METHODS
                   if not callable(getattr(store, name, None))]
        if missing:
            raise TypeError(
                "store must implement WorkflowRepository; missing: " + ", ".join(missing)
            )
        if authorize is not None and not callable(authorize):
            raise TypeError("authorize must be callable.")
        if authorization_gateway is not None and not isinstance(authorization_gateway, AuthorizationGateway):
            raise TypeError("authorization_gateway must be an AuthorizationGateway.")
        if (authorization_gateway is None) != (principal_for_actor is None):
            raise ValueError("authorization_gateway and principal_for_actor must be configured together.")
        if principal_for_actor is not None and not callable(principal_for_actor):
            raise TypeError("principal_for_actor must be callable.")
        if policy_revision is not None and (not isinstance(policy_revision, str) or not policy_revision.strip()):
            raise ValueError("policy_revision must be non-empty text.")
        if authorization_gateway is not None and authorize is not None:
            raise ValueError("configure the central gateway or legacy authorize callback, not both.")
        if workload_credential_verifier is not None and not callable(getattr(workload_credential_verifier, "verify", None)):
            raise TypeError("workload_credential_verifier must implement verify().")
        if workload_credential_verifier is not None and (
            not isinstance(workload_audience, str) or not workload_audience.strip()
        ):
            raise ValueError("workload_audience is required with a credential verifier.")
        if not callable(credential_clock):
            raise TypeError("credential_clock must be callable.")
        if delegation_authority is not None:
            required_delegation_methods = ("create_grant", "verify", "verify_in_transaction", "revoke")
            missing_delegation = [name for name in required_delegation_methods
                                  if not callable(getattr(delegation_authority, name, None))]
            if missing_delegation:
                raise TypeError("delegation_authority is missing capabilities: " + ", ".join(missing_delegation))
        if type(require_workload_delegation) is not bool:
            raise TypeError("require_workload_delegation must be a boolean.")
        if require_workload_delegation and (workload_credential_verifier is None or delegation_authority is None):
            raise ValueError("required workload delegation needs a verifier and delegation authority.")
        if delegation_authority is not None:
            store_identity = getattr(store, "database_identity", None)
            authority_identity = getattr(delegation_authority, "database_identity", None)
            if not isinstance(store_identity, str) or not store_identity or store_identity != authority_identity:
                raise ValueError("atomic delegation enforcement requires the workflow repository and delegation authority to share the same database.")
        self.require_workload_delegation = require_workload_delegation
        self.delegation_authority = delegation_authority
        self.store = store
        self.authorize = authorize
        self.authorization_gateway = authorization_gateway
        self.principal_for_actor = principal_for_actor
        self.policy_revision = policy_revision
        self.workload_credential_verifier = workload_credential_verifier
        self.workload_audience = workload_audience
        self.credential_clock = credential_clock

    @staticmethod
    def _actor(actor_id: str) -> str:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        return actor_id.strip()

    def _allowed(self, actor: str, action: str, task_id: str) -> bool:
        try:
            if self.authorization_gateway is not None:
                principal = self.principal_for_actor(actor)
                if not isinstance(principal, Principal) or principal.principal_id != actor:
                    return False
                request = AuthorizationRequest(
                    principal=principal,
                    action=action,
                    resource="task:" + (task_id if task_id else "collection"),
                    task_id=task_id or None,
                    expected_policy_revision=self.policy_revision,
                )
                return self.authorization_gateway.authorize(request).allowed is True
            if self.authorize is None:
                return False
            return self.authorize(actor, action, task_id) is True
        except Exception:
            return False

    @staticmethod
    def _require_active_lease(run: WorkflowRun, worker_id: str, operation: str) -> str:
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        worker = worker_id.strip()
        if run.state != "running" or run.lease_owner != worker:
            raise WorkflowTransitionError(f"only the current worker may {operation} a running task.")
        if not run.lease_expires_at:
            raise WorkflowConflict("task has no active worker lease.")
        try:
            expiry = datetime.fromisoformat(run.lease_expires_at)
        except (TypeError, ValueError) as exc:
            raise WorkflowConflict("task lease expiry is invalid.") from exc
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise WorkflowConflict("expired or invalid lease cannot modify task.")
        return worker

    def _verify_worker_credential_context(self, run: WorkflowRun, worker_id: str,
                                          credential: str | None, action: str) -> tuple[VerifiedWorkloadContext | None, Callable[[Any], None] | None]:
        """Verify one-use workload proof and return immutable verified identity context.

        Legacy mode is retained only for migration. Production composition must
        configure a verifier and audience so every worker mutation requires a credential.
        """
        if self.workload_credential_verifier is None:
            if credential is not None:
                raise PermissionError("workload credential verifier is not configured.")
            return None, None
        if not isinstance(credential, str) or not credential:
            raise PermissionError("workload credential is required.")
        if not run.lease_generation:
            raise PermissionError("task lease has no cryptographic generation.")
        try:
            now = self.credential_clock()
            if type(now) not in (int, float) or now < 0:
                raise ValueError("invalid credential clock")
            claims = self.workload_credential_verifier.verify(
                credential, expected_audience=self.workload_audience,
                expected_task_id=run.run_id,
                expected_lease_generation=run.lease_generation, now=int(now),
            )
        except Exception as exc:
            raise PermissionError("workload credential rejected.") from exc
        if not isinstance(claims, WorkloadCredentialClaims):
            raise PermissionError("workload credential verifier returned invalid claims.")
        if (
            claims.subject != worker_id
            or claims.task_id != run.run_id
            or (run.tenant_id is not None and claims.tenant_id != run.tenant_id)
            or (getattr(self.store, "supports_tenant_ownership", False) and run.ownership_status != "owned")
            or claims.lease_generation != run.lease_generation
            or claims.audience != self.workload_audience
            or action not in claims.scopes
        ):
            raise PermissionError("workload credential is not authorized for this task action.")

        if self.require_workload_delegation and claims.delegation_id is None:
            raise PermissionError("a workload delegation is required for this task action.")
        if claims.delegation_id is not None:
            if self.delegation_authority is None:
                raise PermissionError("delegation authority is unavailable.")
            try:
                # Preflight is useful for a clear error; the authoritative check
                # is repeated inside the task mutation transaction below.
                self.delegation_authority.verify(
                    claims.delegation_id, grantee_id=claims.subject,
                    tenant_id=claims.tenant_id, agent_id=claims.agent_id,
                    task_id=run.run_id, lease_generation=run.lease_generation,
                    scopes={action}, now=int(now),
                )
            except Exception as exc:
                raise PermissionError("workload delegation is invalid or revoked.") from exc

        # The verified workload identity is a separate authorization subject
        # from the human actor who initiated/owns the task. Never collapse the
        # two identities into one policy check.
        decision = None
        if self.authorization_gateway is not None:
            workload_principal = Principal(
                principal_id=claims.subject,
                kind="workload",
                tenant_id=claims.tenant_id,
                agent_id=claims.agent_id,
                permissions=claims.scopes,
            )
            request = AuthorizationRequest(
                principal=workload_principal,
                action=action,
                resource="task:" + run.run_id,
                task_id=run.run_id,
                delegation_id=claims.delegation_id,
                approval_id=run.approval_id,
                expected_policy_revision=self.policy_revision,
            )
            try:
                decision = self.authorization_gateway.authorize(request)
            except Exception as exc:
                raise PermissionError("workload authorization is unavailable.") from exc
            if getattr(decision, "allowed", None) is not True:
                raise PermissionError("workload principal is not authorized for this task action.")
        fence = None
        if claims.delegation_id is not None:
            authority = self.delegation_authority
            context = {
                "delegation_id": claims.delegation_id,
                "grantee_id": claims.subject,
                "tenant_id": claims.tenant_id,
                "agent_id": claims.agent_id,
                "task_id": run.run_id,
                "lease_generation": run.lease_generation,
                "scopes": {action},
                "now": int(now),
            }
            def fence(db):
                authority.verify_in_transaction(db, **context)
            fence.__name__ = "delegation_transaction_fence"
        verified = VerifiedWorkloadContext(
            issuer=claims.issuer, subject=claims.subject, tenant_id=claims.tenant_id,
            owner_principal_id=run.owner_principal_id or "", agent_id=claims.agent_id,
            audience=claims.audience, token_id=claims.token_id,
            task_id=claims.task_id, lease_generation=claims.lease_generation,
            scopes=claims.scopes, delegation_id=claims.delegation_id,
            expires_at=claims.expires_at, authorized_action=action,
            authorization_decision_id=(decision.decision_id if decision is not None else ""),
            policy_revision=(decision.policy_revision if decision is not None else ""),
            delegation_fence=fence,
        )
        return verified, fence

    def _verify_worker_credential(self, run: WorkflowRun, worker_id: str,
                                  credential: str | None, action: str):
        """Compatibility adapter for existing task mutations."""
        context, fence = self._verify_worker_credential_context(run, worker_id, credential, action)
        return (context.lease_generation if context is not None else None), fence

    def verify_workload_context(self, *, task_id: str, worker_id: str,
                                credential: str, action: str) -> VerifiedWorkloadContext:
        """Return verified workload identity for a narrowly scoped downstream action."""
        if not getattr(self.store, "supports_tenant_ownership", False):
            raise PermissionError("verified_external_action_context_requires_owned_postgres_task")
        if self.authorization_gateway is None:
            raise PermissionError("verified_external_action_context_requires_central_authorization")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        if run.ownership_status != "owned" or not run.tenant_id or not run.owner_principal_id:
            raise PermissionError("legacy_unassigned_task_cannot_authorize_external_actions")
        worker = self._require_active_lease(run, worker_id, "perform an authorized action for")
        context, _ = self._verify_worker_credential_context(run, worker, credential, action)
        if context is None:
            raise PermissionError("verified_workload_context_unavailable")
        return context

    def active_lease(self, task_id: str):
        """Expose only a current lease snapshot for the trusted credential issuer."""
        from core.workload_credential_issuer import ActiveTaskLease
        run = self.store.get(task_id)
        if run is None or run.state != "running" or not run.lease_owner or not run.lease_generation:
            return None
        try:
            expiry = datetime.fromisoformat(run.lease_expires_at or "")
        except (TypeError, ValueError):
            return None
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            return None
        return ActiveTaskLease(run.run_id, run.lease_owner, run.lease_generation, int(expiry.timestamp()))

    @staticmethod
    def _record(run: WorkflowRun) -> TaskRecord:
        return TaskRecord(run.run_id, run.workflow_name, run.state, run.definition, run.result,
                          run.error_code, run.version, run.attempts, run.created_at, run.updated_at,
                          run.lease_owner, run.lease_expires_at)

    def enqueue(self, *, actor_id: str, idempotency_key: str, name: str, steps: list[dict[str, Any]]) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:create", ""):
            raise PermissionError("task creation is not authorized.")
        ownership = {}
        if getattr(self.store, "supports_tenant_ownership", False):
            if self.authorization_gateway is None or self.principal_for_actor is None:
                raise PermissionError("tenant_owned_task_creation_requires_central_authorization")
            principal = self.principal_for_actor(actor)
            if (not isinstance(principal, Principal) or principal.principal_id != actor
                    or principal.kind != "human"
                    or not isinstance(principal.tenant_id, str) or not principal.tenant_id.strip()):
                raise PermissionError("authenticated_task_owner_tenant_context_required")
            ownership = {"tenant_id": principal.tenant_id, "owner_principal_id": principal.principal_id}
        run = self.store.create(
            idempotency_key=idempotency_key, workflow_name=name, definition=steps, **ownership
        )
        existing = self.store.list_task_events(run.run_id, limit=1)
        if not existing:
            self.store.append_task_event(run.run_id, kind="task.created", actor_id=actor,
                                         payload={"name": run.workflow_name, "step_count": len(run.definition)},
                                         expected_version=run.version)
        return self._record(self.store.get(run.run_id))

    def list_tasks(
        self, *, actor_id: str, state: str | None = None, limit: int = 50,
    ) -> list[TaskRecord]:
        """List recent tasks for an explicitly authorized supervisor/dashboard.

        Listing is a separate capability from reading a known task ID because it
        exposes task existence and metadata across the runtime. Results are newest
        first and bounded by the store to at most 100 records.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:list", ""):
            raise PermissionError("task listing is not authorized.")
        return [self._record(run) for run in self.store.list_runs(state=state, limit=limit)]

    def list_task_summaries(
        self, *, actor_id: str, state: str | None = None, limit: int = 50,
    ) -> list[TaskSummary]:
        """List redacted task metadata without exposing definitions or results.

        Requires the dedicated task:list capability. A caller must separately
        hold task:read for any task detail or event stream it subsequently opens.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:list", ""):
            raise PermissionError("task listing is not authorized.")
        runs = self.store.list_runs(state=state, limit=limit)
        return [
            TaskSummary(
                task_id=run.run_id,
                name=run.workflow_name,
                state=run.state,
                version=run.version,
                attempts=run.attempts,
                created_at=run.created_at,
                updated_at=run.updated_at,
                lease_owner=run.lease_owner,
                lease_expires_at=run.lease_expires_at,
            )
            for run in runs
        ]

    def get(self, *, actor_id: str, task_id: str) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:read", task_id):
            raise PermissionError("task read is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        return self._record(run)

    def get_by_idempotency_key(self, *, actor_id: str, idempotency_key: str) -> TaskRecord | None:
        """Recover an existing task by key without bypassing task-read authorization."""
        actor = self._actor(actor_id)
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 200
            or "\x00" in idempotency_key
        ):
            raise ValueError("idempotency_key must be non-empty bounded text.")
        run = self.store.get_by_idempotency_key(idempotency_key.strip())
        if run is None:
            return None
        if not self._allowed(actor, "task:read", run.run_id):
            raise PermissionError("task read is not authorized.")
        if getattr(self.store, "supports_tenant_ownership", False):
            principal = self.principal_for_actor(actor) if self.principal_for_actor else None
            if (
                not isinstance(principal, Principal)
                or principal.principal_id != actor
                or run.ownership_status != "owned"
                or run.tenant_id != principal.tenant_id
                or run.owner_principal_id != principal.principal_id
            ):
                raise PermissionError("task ownership does not match the authenticated principal.")
        return self._record(run)

    def approve(self, *, actor_id: str, task_id: str, session_id: str, expected_version: int) -> TaskRecord:
        """Approve the exact queued workflow version for the authenticated session."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:approve", task_id):
            raise PermissionError("task approval is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        approved = self.store.approve_with_event(
            task_id, actor_id=actor, session_id=session_id,
            expected_version=expected_version,
        )
        return self._record(approved)

    def revoke_session_approvals(self, session_id: str) -> list[TaskRecord]:
        """Cancel queued tasks whose approval was granted by the exact revoked session."""
        import hashlib
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512 or chr(0) in session_id:
            raise ValueError("session_id must be non-empty bounded text.")
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        revoked: list[TaskRecord] = []
        # Page by stable creation cursor, not a fixed recent-task window. Only
        # queued tasks approved by this exact session are ever considered.
        cursor: tuple[str, str] | None = None
        while True:
            page = self.store.list_queued_approved_by_session(session_hash, limit=100, after=cursor)
            if not page:
                break
            for run in page:
                cursor = (run.created_at, run.run_id)
                if not run.approved_by or not self._allowed(run.approved_by, "task:cancel", run.run_id):
                    continue
                try:
                    cancelled = self.store.transition_with_event(
                        run.run_id, expected_version=run.version,
                        to_state="cancelled", error_code="session_revoked",
                        event_kind="task.cancelled", actor_id=run.approved_by,
                        event_payload={"state_version": run.version + 1, "reason": "session_revoked"},
                    )
                except WorkflowConflict:
                    # A concurrent worker/state change won the race; never
                    # overwrite it with a stale logout cancellation.
                    continue
                revoked.append(self._record(cancelled))
        return revoked

    def events(
        self, *, actor_id: str, task_id: str, after_sequence: int = 0, limit: int = 100,
    ) -> list[TaskEvent]:
        """Read a bounded page of the authorized task event stream.

        Clients can poll incrementally by passing the last observed event sequence
        as after_sequence. Event payloads remain behind task:read authorization.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:read", task_id):
            raise PermissionError("task event read is not authorized.")
        return self.store.list_task_events(
            task_id, after_sequence=after_sequence, limit=limit,
        )

    def record_progress(self, *, actor_id: str, task_id: str, worker_id: str,
                        expected_version: int, message: str,
                        percent: int | None = None,
                        workload_credential: str | None = None) -> TaskRecord:
        """Record progress only for the worker holding the current unexpired lease.

        worker_id selects the lease record; when a verifier is configured, the
        one-use credential must cryptographically bind its subject to that owner.
        Production composition must configure the verifier and audience.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:progress", task_id):
            raise PermissionError("task progress update is not authorized.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        worker = worker_id.strip()
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(message, str) or not message.strip() or len(message) > 1000 or "\x00" in message:
            raise ValueError("message must be non-empty bounded text.")
        if percent is not None and (type(percent) is not int or not 0 <= percent <= 100):
            raise ValueError("percent must be an integer from 0 to 100.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        if run.state != "running":
            raise WorkflowTransitionError("progress can only be recorded for a running task.")
        if run.lease_owner != worker:
            raise WorkflowTransitionError("only the current worker may record task progress.")
        if not run.lease_expires_at:
            raise WorkflowConflict("task has no active worker lease.")
        try:
            lease_expiry = datetime.fromisoformat(run.lease_expires_at)
        except (TypeError, ValueError) as exc:
            raise WorkflowConflict("task lease expiry is invalid.") from exc
        if lease_expiry.tzinfo is None or lease_expiry <= datetime.now(timezone.utc):
            raise WorkflowConflict("expired or invalid lease cannot record task progress.")
        lease_generation, delegation_fence = self._verify_worker_credential(run, worker, workload_credential, "task:progress")
        self.store.append_task_event(task_id, kind="task.progress", actor_id=actor,
                                     payload={"worker_id": worker, "message": message.strip(), "percent": percent},
                                     expected_version=expected_version, expected_lease_owner=worker,
                                     expected_lease_generation=lease_generation, delegation_fence=delegation_fence)
        return self._record(self.store.get(task_id))

    def renew_lease(
        self, *, actor_id: str, task_id: str, worker_id: str,
        expected_version: int, lease_seconds: int = 60,
        workload_credential: str | None = None,
    ) -> TaskRecord:
        """Renew the current worker lease with authorization and compare-and-swap."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:lease:renew", task_id):
            raise PermissionError("task lease renewal is not authorized.")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200 or "\x00" in worker_id:
            raise ValueError("worker_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        current = self.store.get(task_id)
        if current is None:
            raise KeyError("task not found.")
        worker = self._require_active_lease(current, worker_id, "renew")
        _, delegation_fence = self._verify_worker_credential(current, worker, workload_credential, "task:lease:renew")
        renewed = self.store.renew_lease(
            task_id, worker_id=worker,
            expected_version=expected_version, lease_seconds=lease_seconds,
            delegation_fence=delegation_fence,
        )
        return self._record(renewed)

    def record_artifact(self, *, actor_id: str, task_id: str, worker_id: str,
                        expected_version: int, artifact_type: str, reference: str,
                        metadata: dict[str, Any] | None = None,
                        workload_credential: str | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:artifact", task_id):
            raise PermissionError("task artifact update is not authorized.")
        if not isinstance(artifact_type, str) or not _CODE.fullmatch(artifact_type):
            raise ValueError("artifact_type must be a bounded identifier.")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 2000 or "\x00" in reference:
            raise ValueError("reference must be non-empty bounded text.")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        worker = self._require_active_lease(run, worker_id, "attach artifacts to")
        lease_generation, delegation_fence = self._verify_worker_credential(run, worker, workload_credential, "task:artifact")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        self.store.append_task_event(task_id, kind="task.artifact", actor_id=actor,
                                     payload={"worker_id": worker, "artifact_type": artifact_type,
                                              "reference": reference.strip(), "metadata": metadata},
                                     expected_version=expected_version, expected_lease_owner=worker,
                                     expected_lease_generation=lease_generation, delegation_fence=delegation_fence)
        return self._record(self.store.get(task_id))

    def complete(self, *, actor_id: str, task_id: str, expected_version: int,
                 worker_id: str, result: dict[str, Any] | list[Any],
                 workload_credential: str | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:complete", task_id):
            raise PermissionError("task completion is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        worker = self._require_active_lease(run, worker_id, "complete")
        lease_generation, delegation_fence = self._verify_worker_credential(run, worker, workload_credential, "task:complete")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        completed = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="succeeded",
            result=result, event_kind="task.completed", actor_id=actor,
            event_payload={"state_version": expected_version + 1, "worker_id": worker},
            expected_lease_owner=worker, expected_lease_generation=lease_generation,
            delegation_fence=delegation_fence,
        )
        return self._record(completed)

    def fail(self, *, actor_id: str, task_id: str, expected_version: int,
             worker_id: str, error_code: str,
             workload_credential: str | None = None) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:fail", task_id):
            raise PermissionError("task failure update is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        worker = self._require_active_lease(run, worker_id, "fail")
        lease_generation, delegation_fence = self._verify_worker_credential(run, worker, workload_credential, "task:fail")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(error_code, str) or not _CODE.fullmatch(error_code):
            raise ValueError("error_code must be a bounded identifier.")
        failed = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="failed",
            error_code=error_code, event_kind="task.failed", actor_id=actor,
            event_payload={"error_code": error_code, "state_version": expected_version + 1,
                           "worker_id": worker},
            expected_lease_owner=worker, expected_lease_generation=lease_generation,
            delegation_fence=delegation_fence,
        )
        return self._record(failed)



    def retry_failed(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        """Explicitly enqueue a fresh task from failed/interrupted work; never replay in place.

        The caller must be separately authorized for task:retry. The new run receives
        a distinct idempotency key and fresh approval state, so approval-required work
        must be approved again before a worker can claim it.
        """
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:retry", task_id):
            raise PermissionError("task retry is not authorized.")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise ValueError("task_id must be non-empty bounded text.")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        original = self.store.get(task_id)
        if original is None:
            raise KeyError("task not found.")
        if original.version != expected_version:
            raise WorkflowConflict("task version changed; reload before retrying.")
        if original.state not in {"failed", "interrupted"}:
            raise WorkflowTransitionError("only failed or interrupted tasks can be retried.")
        # A stable key makes duplicate submissions for the same observed version idempotent.
        retry_key = f"retry:{original.run_id}:{expected_version}"
        retried = self.store.create(
            idempotency_key=retry_key,
            workflow_name=original.workflow_name,
            definition=original.definition,
        )
        existing_events = self.store.list_task_events(retried.run_id, limit=1)
        if not existing_events:
            self.store.append_task_event(
                retried.run_id, kind="task.created", actor_id=actor,
                payload={"name": retried.workflow_name, "step_count": len(retried.definition),
                         "retry_of": original.run_id, "source_state": original.state,
                         "source_version": original.version,
                         "approval_required": any(step.get("requires_approval", True) is True for step in original.definition)},
                expected_version=retried.version,
            )
        return self._record(self.store.get(retried.run_id))

    def recover_expired(self, *, actor_id: str, limit: int = 50) -> list[TaskRecord]:
        """Mark expired worker leases interrupted and journal each recovery; never replay them."""
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:recover", ""):
            raise PermissionError("task recovery is not authorized.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        interrupted = self.store.recover_expired_with_events(actor_id=actor, limit=limit)
        return [self._record(run) for run in interrupted]

    def cancel(self, *, actor_id: str, task_id: str, expected_version: int) -> TaskRecord:
        actor = self._actor(actor_id)
        if not self._allowed(actor, "task:cancel", task_id):
            raise PermissionError("task cancellation is not authorized.")
        run = self.store.get(task_id)
        if run is None:
            raise KeyError("task not found.")
        cancelled = self.store.transition_with_event(
            task_id, expected_version=expected_version, to_state="cancelled",
            error_code="cancelled", event_kind="task.cancelled", actor_id=actor,
            event_payload={"state_version": expected_version + 1},
        )
        return self._record(cancelled)
