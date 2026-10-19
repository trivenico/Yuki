"""Approval coordinator for human-in-the-loop action verification and execution."""
import copy
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class PendingItem:
    pending_id: str
    action: dict
    actor: str
    session_id: str


@dataclass
class ExecutionResult:
    ok: bool
    status: str
    result: Any = None


class ApprovalCoordinator:
    """One-time, actor-bound approvals with fail-closed audit transitions."""

    def __init__(self, pending_store=None, audit_log=None, max_pending: int = 128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        self.pending_store = pending_store
        self.audit_log = audit_log
        self.max_pending = max_pending
        self._in_memory_pending = {}
        self._lock = threading.RLock()

    def _record_event(self, event_type: str, pending_id: str, actor: str, session_id: str) -> None:
        if self.audit_log is not None:
            try:
                self.audit_log.record_event(event_type, pending_id, actor, session_id)
            except Exception as exc:
                raise RuntimeError("Approval audit unavailable; operation denied.") from exc

    def _discard_pending(self, pending_id: str) -> None:
        if self.pending_store is not None:
            self.pending_store.deny(pending_id)
        else:
            self._in_memory_pending.pop(pending_id, None)

    def record(self, planner, result, actor: str, session_id: str) -> PendingItem:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("Trusted actor identity is required.")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("Trusted session identity is required.")
        action = getattr(
            result,
            "action",
            {
                "skill_id": getattr(result, "skill_id", ""),
                "arguments": getattr(result, "arguments", {}),
            },
        )
        if not isinstance(action, dict):
            raise ValueError("Proposed action must be an object.")
        snapshot = copy.deepcopy(action)

        with self._lock:
            if self.pending_store is None and len(self._in_memory_pending) >= self.max_pending:
                raise RuntimeError("Pending capacity reached.")

            if self.pending_store is not None:
                stored = self.pending_store.put(
                    skill_id=snapshot.get("skill_id", ""),
                    arguments=snapshot.get("arguments", {}),
                    action=snapshot,
                    actor=actor,
                    session_id=session_id,
                )
                pending_id = stored.pending_id
            else:
                pending_id = uuid.uuid4().hex
                self._in_memory_pending[pending_id] = PendingItem(
                    pending_id, snapshot, actor, session_id
                )

            try:
                self._record_event("requested", pending_id, actor, session_id)
            except Exception:
                try:
                    self._discard_pending(pending_id)
                except Exception as cleanup_exc:
                    raise RuntimeError(
                        "Approval audit failed and pending cleanup failed; action remains denied."
                    ) from cleanup_exc
                raise

        return PendingItem(pending_id, copy.deepcopy(snapshot), actor, session_id)

    def approve(
        self,
        planner,
        pending_id: str,
        actor: str,
        session_id: str,
        confirm: Callable[[dict], bool],
    ) -> ExecutionResult:
        # Atomically claim an in-memory item so two callers cannot execute it twice.
        with self._lock:
            if self.pending_store is not None:
                consumed = self.pending_store.consume(
                    pending_id, actor=actor, session_id=session_id
                )
                if consumed is None:
                    return ExecutionResult(ok=False, status="pending_not_found")
                action_snapshot = copy.deepcopy(consumed.action)
            else:
                item = self._in_memory_pending.get(pending_id)
                if item is None or item.actor != actor or item.session_id != session_id:
                    return ExecutionResult(ok=False, status="pending_not_found")
                del self._in_memory_pending[pending_id]
                action_snapshot = copy.deepcopy(item.action)

        try:
            accepted = confirm(copy.deepcopy(action_snapshot)) is True
        except Exception:
            accepted = False

        if not accepted:
            try:
                self._record_event("denied", pending_id, actor, session_id)
            except RuntimeError:
                return ExecutionResult(ok=False, status="audit_unavailable")
            return ExecutionResult(ok=False, status="approval_denied")

        # Durable approval and intent-to-execute records must precede side effects.
        try:
            self._record_event("approved", pending_id, actor, session_id)
            self._record_event("execution_started", pending_id, actor, session_id)
        except RuntimeError:
            return ExecutionResult(ok=False, status="audit_unavailable")

        try:
            result = planner.execute_proposed(action_snapshot)
        except Exception:
            try:
                self._record_event("execution_failed", pending_id, actor, session_id)
            except RuntimeError:
                return ExecutionResult(ok=False, status="execution_failed_audit_unavailable")
            return ExecutionResult(ok=False, status="execution_failed")

        try:
            self._record_event("executed", pending_id, actor, session_id)
        except RuntimeError:
            # Execution may already have happened. Report this explicitly rather
            # than falsely claiming the action was denied or rolled back.
            return ExecutionResult(ok=False, status="executed_audit_failed", result=result)

        return ExecutionResult(ok=True, status="completed", result=result)
