"""Per-owner durable approval storage and trusted executor resolution.

Each authenticated owner receives a private SQLite pending store and audit log.
The model never supplies a storage path, coordinator, actor, or executor.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading
from typing import Any, Callable

from core.approval_audit import SQLiteApprovalAuditLog
from core.approval_flow import ApprovalCoordinator
from core.approval_runtime_adapter import ApprovalRuntimeAdapter
from core.guarded_tool_runtime import GuardedToolRuntime
from core.pending_approvals import SQLitePendingApprovalStore
from core.operation_context import OperationExecutionContext


class _OwnerBoundApprovedToolExecutor:
    """Bind approved execution to the exact owner/session guarded registry."""

    def __init__(self, runtime: GuardedToolRuntime, owner_id: str, session_id: str):
        if not isinstance(runtime, GuardedToolRuntime):
            raise TypeError("approved execution requires a GuardedToolRuntime")
        if not runtime.audit_required or not callable(runtime.audit_callback):
            raise ValueError("approved execution requires a durable-audit guarded runtime")
        self._runtime = runtime
        self._owner_id = owner_id
        self._session_id = session_id

    def execute_approved_action(self, action):
        if not isinstance(action, dict):
            raise ValueError("approved action must be an object")
        context = action.get("execution_context")
        if (not isinstance(context, dict)
                or context.get("actor") != self._owner_id
                or context.get("session_id") != self._session_id):
            raise PermissionError("approved action belongs to a different owner or session")
        return self._runtime.execute_approved_action(action, execution_context=context)


class OwnerApprovalRuntimeFactory:
    """Create cached, owner-isolated durable approval coordinators and adapters.

    `planner_resolver_for_owner(owner_id, session_id)` is a trusted host
    callback. It must return the exact owner/session-bound GuardedToolRuntime,
    or None when execution is unavailable. Arbitrary executor objects are
    rejected so approval cannot bypass the registered handler, policy or audit
    boundary. The factory refuses to resolve a runtime for another actor.
    """

    def __init__(
        self,
        storage_dir: str | Path,
        *,
        planner_resolver_for_owner: Callable[[str, str], Any],
        ttl_seconds: int = 300,
        max_pending: int = 128,
    ) -> None:
        if not callable(planner_resolver_for_owner):
            raise TypeError("planner_resolver_for_owner must be callable")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096")
        requested = Path(storage_dir).expanduser()
        if requested.exists() and requested.is_symlink():
            raise ValueError("approval storage directory must not be a symlink")
        requested.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not requested.is_dir():
            raise ValueError("approval storage path must be a directory")
        self._root = requested.resolve()
        if os.name == "posix":
            os.chmod(self._root, 0o700)
        self._planner_resolver_for_owner = planner_resolver_for_owner
        self._ttl_seconds = ttl_seconds
        self._max_pending = max_pending
        self._entries: dict[str, tuple[ApprovalCoordinator, ApprovalRuntimeAdapter]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _validate_owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("an authenticated bounded owner identity is required")
        return owner_id

    def coordinator_for_owner(self, owner_id: str) -> ApprovalCoordinator:
        """Return the durable coordinator for one authenticated owner."""
        owner_id = self._validate_owner(owner_id)
        with self._lock:
            existing = self._entries.get(owner_id)
            if existing is not None:
                return existing[0]

            # Hash owner identifiers so filesystem paths reveal no account IDs.
            owner_key = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
            owner_dir = self._root / owner_key
            owner_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if owner_dir.is_symlink() or not owner_dir.is_dir():
                raise ValueError("owner approval storage directory is unsafe")
            if os.name == "posix":
                os.chmod(owner_dir, 0o700)

            pending_store = SQLitePendingApprovalStore(
                str(owner_dir / "pending.sqlite3"),
                ttl_seconds=self._ttl_seconds,
                max_pending=self._max_pending,
            )
            audit_log = SQLiteApprovalAuditLog(str(owner_dir / "audit.sqlite3"))

            def resolve(*, actor: str, session_id: str):
                # The authenticated actor is checked again at the execution
                # boundary; never let a coordinator be used as another owner.
                if actor != owner_id:
                    return None
                candidate = self._planner_resolver_for_owner(owner_id, session_id)
                # An arbitrary object exposing execute_approved_action is not
                # an authorization boundary. Only the exact guarded registry
                # may execute an approved action.
                if not isinstance(candidate, GuardedToolRuntime):
                    return None
                return _OwnerBoundApprovedToolExecutor(candidate, owner_id, session_id)

            coordinator = ApprovalCoordinator(
                pending_store=pending_store,
                audit_log=audit_log,
                planner_resolver=resolve,
                max_pending=self._max_pending,
            )
            adapter = ApprovalRuntimeAdapter(coordinator)
            self._entries[owner_id] = (coordinator, adapter)
            return coordinator


    def bind_provider_for_owner(
        self, owner_id: str, context: OperationExecutionContext, provider_id: str,
    ):
        """Return the durable provider binding for this authenticated owner.

        The callback is suitable for ProviderOperationAdapter(bind_provider=...).
        It never accepts owner/session identity from the model or provider payload.
        """
        owner_id = self._validate_owner(owner_id)
        if not isinstance(context, OperationExecutionContext):
            raise TypeError("a trusted operation execution context is required")
        if context.actor != owner_id:
            raise PermissionError("provider operation context belongs to another owner")
        coordinator = self.coordinator_for_owner(owner_id)
        store = coordinator.pending_store
        binder = getattr(store, "bind_operation_provider", None)
        if not callable(binder):
            raise RuntimeError("durable provider binding is unavailable")
        return binder(
            context.pending_id,
            actor=owner_id,
            session_id=context.session_id,
            operation_id=context.operation_id,
            action_digest=context.action_digest,
            provider_id=provider_id,
        )

    def provider_binder_for_owner(self, owner_id: str):
        """Return a closure bound to one owner for trusted provider adapter setup."""
        owner_id = self._validate_owner(owner_id)

        def bind(context: OperationExecutionContext, provider_id: str):
            return self.bind_provider_for_owner(owner_id, context, provider_id)

        return bind

    def adapter_for_owner(self, owner_id: str) -> ApprovalRuntimeAdapter:
        """Return the stable approval callback to inject into that owner's tool runtime."""
        self.coordinator_for_owner(owner_id)
        with self._lock:
            return self._entries[owner_id][1]
