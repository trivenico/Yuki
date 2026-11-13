"""Durable, one-time approval coordinator for guarded task execution."""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class ApprovalOutcome:
    ok: bool
    status: str
    pending_id: str | None = None
    result: Any = None


class ApprovalCoordinator:
    def __init__(self, *, pending_store=None, audit_log=None, planner_resolver=None, max_pending: int = 128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        if audit_log is None or not callable(getattr(audit_log, "append", None)):
            raise ValueError("a required approval audit log with append() is required")
        self.pending_store = pending_store
        self.audit_log = audit_log
        if not callable(planner_resolver):
            raise ValueError("a trusted host planner_resolver is required")
        self.planner_resolver = planner_resolver
        self.max_pending = max_pending
        self._pending: dict[str, dict] = {}
        self._pending_lock = threading.Lock()

    @staticmethod
    def _extract_action(planner, result) -> tuple[str, dict, dict]:
        action = getattr(result, "action", None)
        if action is None and isinstance(result, dict):
            action = result.get("action")
        if not isinstance(action, dict):
            candidate = getattr(result, "data", None)
            if isinstance(candidate, dict):
                action = candidate.get("action")
        if not isinstance(action, dict):
            raise ValueError("Task result contains no pending action.")
        skill_id = action.get("skill_id")
        arguments = action.get("arguments")
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120 or not isinstance(arguments, dict):
            raise ValueError("Pending action has an invalid skill id or arguments.")
        import json
        try:
            snapshot = json.loads(json.dumps(action, allow_nan=False, ensure_ascii=False))
            args_snapshot = json.loads(json.dumps(arguments, allow_nan=False, ensure_ascii=False))
            if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
                raise ValueError("Pending action exceeds 16 KiB.")
        except (TypeError, ValueError) as exc:
            raise ValueError("Pending action must be JSON-safe and no larger than 16 KiB.") from exc
        return skill_id, args_snapshot, snapshot

    def record(self, planner, result, *, actor: str, session_id: str):
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 256:
            raise ValueError("A bounded actor identity is required.")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
            raise ValueError("A bounded session identity is required.")
        skill_id, arguments, action = self._extract_action(planner, result)
        if self.pending_store is not None:
            stored = self.pending_store.put(
                skill_id, arguments, action, actor=actor, session_id=session_id
            )
            pending_id = stored.pending_id
        else:
            import secrets
            with self._pending_lock:
                if len(self._pending) >= self.max_pending:
                    raise RuntimeError("Pending approval capacity reached.")
                pending_id = secrets.token_urlsafe(24)
                self._pending[pending_id] = {
                    "skill_id": skill_id, "arguments": arguments, "action": action,
                    "actor": actor, "session_id": session_id,
                }
        try:
            self._audit("requested", pending_id, actor, skill_id)
        except Exception as exc:
            # Never leave an un-audited approval request available for later execution.
            if self.pending_store is not None:
                try:
                    self.pending_store.deny(pending_id)
                except Exception:
                    pass
            else:
                with self._pending_lock:
                    self._pending.pop(pending_id, None)
            raise RuntimeError(
                "Approval request could not be audited; no approval was issued."
            ) from exc
        return ApprovalOutcome(False, "pending_approval", pending_id=pending_id, result=action)

    def approve(self, planner, pending_id: str, *, actor: str, session_id: str,
                confirm: Callable[[dict], bool]) -> ApprovalOutcome:
        pending = self._consume(pending_id, actor=actor, session_id=session_id)
        if pending is None:
            return ApprovalOutcome(False, "pending_not_found", pending_id=pending_id if isinstance(pending_id, str) else None)
        action = pending["action"]
        import json
        display_action = json.loads(json.dumps(action))
        try:
            confirmed = confirm(display_action) is True
        except Exception:
            confirmed = False
        if not confirmed:
            try:
                self._audit("denied", pending_id, actor, pending["skill_id"])
            except Exception:
                return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)
            return ApprovalOutcome(False, "approval_denied", pending_id=pending_id)

        # The request-supplied planner is deliberately never an execution
        # authority. Resolve the executor exclusively from trusted host state
        # using the authenticated actor/session bound to this approval.
        try:
            executor = self.planner_resolver(actor=actor, session_id=session_id)
        except Exception:
            executor = None
        target = (
            getattr(getattr(executor, "agent", None), "execute_approved_action", None)
            if executor is not None else None
        )
        if not callable(target) and executor is not None:
            target = getattr(executor, "execute_approved_action", None)
        if not callable(target):
            try:
                self._audit("execution_unavailable", pending_id, actor, pending["skill_id"])
            except Exception:
                return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)
            return ApprovalOutcome(False, "execution_unavailable", pending_id=pending_id)

        try:
            self._audit("approved", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)

        # Persist a start marker before dispatch. If this write fails, the
        # executor is never called; after it succeeds, recovery can distinguish
        # an action that may have crossed the execution boundary.
        try:
            self._audit("execution_started", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)

        try:
            result = target(action)
        except Exception as exc:
            # An executor exception does not prove that no external side effect
            # occurred: a provider may have committed the action before a
            # timeout, transport reset, or response-decoding failure. Treat the
            # result as indeterminate and never label it safe to retry.
            try:
                self._audit("outcome_unknown", pending_id, actor, pending["skill_id"])
            except Exception:
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"execution_error": type(exc).__name__, "outcome": "unknown",
                            "audit": "unavailable"},
                )
            return ApprovalOutcome(
                False, "recovery_required", pending_id=pending_id,
                result={"execution_error": type(exc).__name__, "outcome": "unknown"},
            )

        try:
            self._audit("executed", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(
                False, "recovery_required", pending_id=pending_id,
                result={"execution_result": result, "audit": "unavailable"},
            )
        return ApprovalOutcome(bool(getattr(result, "ok", True)), "completed", pending_id=pending_id, result=result)

    def _consume(self, pending_id, *, actor, session_id):
        if not isinstance(pending_id, str) or not pending_id:
            return None
        if not isinstance(actor, str) or not isinstance(session_id, str):
            return None
        if self.pending_store is not None:
            item = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
            if item is None:
                return None
            return {"skill_id": item.skill_id, "arguments": item.arguments,
                    "action": item.action, "actor": actor, "session_id": session_id}
        # Check identity and remove the approval under one lock so two concurrent
        # callers cannot both observe and execute the same pending action.
        with self._pending_lock:
            item = self._pending.get(pending_id)
            if item is None or item["actor"] != actor or item["session_id"] != session_id:
                return None
            return self._pending.pop(pending_id)

    def _audit(self, event_type, pending_id, actor, skill_id):
        self.audit_log.append(event_type=event_type, pending_id=pending_id,
                              actor=actor, skill_id=skill_id)
