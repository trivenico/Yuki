"""Durable, one-time approval coordinator for guarded task execution.

The coordinator delegates pending state and atomic consumption to the configured
store. The confirm callback is only a presentation/confirmation hook; execution
uses the stored action snapshot, never a callback-mutated object.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ApprovalOutcome:
    ok: bool
    status: str
    pending_id: str | None = None
    result: Any = None


class ApprovalCoordinator:
    def __init__(self, *, pending_store=None, audit_log=None, max_pending: int = 128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        self.pending_store = pending_store
        self.audit_log = audit_log
        self.max_pending = max_pending
        self._pending: dict[str, dict] = {}

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
        if not isinstance(skill_id, str) or not skill_id.strip() or not isinstance(arguments, dict):
            raise ValueError("Pending action has an invalid skill id or arguments.")
        import json
        try:
            snapshot = json.loads(json.dumps(action, allow_nan=False, ensure_ascii=False))
            args_snapshot = json.loads(json.dumps(arguments, allow_nan=False, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("Pending action must be JSON-safe.") from exc
        return skill_id, args_snapshot, snapshot

    def record(self, planner, result, *, actor: str, session_id: str):
        skill_id, arguments, action = self._extract_action(planner, result)
        if self.pending_store is not None:
            stored = self.pending_store.put(
                skill_id, arguments, action, actor=actor, session_id=session_id
            )
            pending_id = stored.pending_id
        else:
            if len(self._pending) >= self.max_pending:
                raise RuntimeError("Pending approval capacity reached.")
            import secrets
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
                    # The store may be unavailable; do not issue or return the token.
                    pass
            else:
                self._pending.pop(pending_id, None)
            raise RuntimeError(
                "Approval request could not be audited; no approval was issued."
            ) from exc
        return ApprovalOutcome(False, "pending_approval", pending_id=pending_id, result=action)

    def approve(self, planner, pending_id: str, *, actor: str, session_id: str,
                confirm: Callable[[dict], bool]) -> ApprovalOutcome:
        pending = self._consume(pending_id, actor=actor, session_id=session_id)
        if pending is None:
            return ApprovalOutcome(False, "pending_not_found", pending_id=pending_id)
        action = pending["action"]
        # The UI receives a copy. Mutating it cannot alter the stored execution snapshot.
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

        # Approval must be durably recorded before any side effect is attempted.
        try:
            self._audit("approved", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)

        try:
            result = (
                planner.agent.execute_approved_action(action)
                if hasattr(planner, "agent") and hasattr(planner.agent, "execute_approved_action")
                else planner.execute_approved_action(action)
            )
        except Exception as exc:
            try:
                self._audit("execution_failed", pending_id, actor, pending["skill_id"])
            except Exception:
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"execution_error": type(exc).__name__, "audit": "unavailable"},
                )
            return ApprovalOutcome(False, "execution_failed", pending_id=pending_id, result=type(exc).__name__)

        # The external side effect may already have happened. If its receipt cannot
        # be persisted, never report an ordinary failure that could trigger replay.
        try:
            self._audit("executed", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(
                False, "recovery_required", pending_id=pending_id,
                result={"execution_result": result, "audit": "unavailable"},
            )
        return ApprovalOutcome(bool(getattr(result, "ok", True)), "completed", pending_id=pending_id, result=result)

    def _consume(self, pending_id, *, actor, session_id):
        if self.pending_store is not None:
            item = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
            if item is None:
                return None
            return {"skill_id": item.skill_id, "arguments": item.arguments,
                    "action": item.action, "actor": actor, "session_id": session_id}
        item = self._pending.get(pending_id)
        if item is None or item["actor"] != actor or item["session_id"] != session_id:
            return None
        return self._pending.pop(pending_id)

    def _audit(self, event_type, pending_id, actor, skill_id):
        if self.audit_log is not None:
            self.audit_log.append(event_type=event_type, pending_id=pending_id,
                                  actor=actor, skill_id=skill_id)
