"""Durable, one-time approval coordinator for guarded task execution.

Audit logging is mandatory: an approval request or execution without a durable audit
sink must fail closed. The confirmation callback is presentation-only; execution uses
the stored action snapshot, never a callback-mutated object.
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
    def __init__(self, *, pending_store=None, audit_log=None, execution_ledger=None, max_pending: int = 128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        if pending_store is None or not callable(getattr(pending_store, "put", None)) or not callable(getattr(pending_store, "consume", None)) or not callable(getattr(pending_store, "deny", None)):
            raise ValueError("A durable pending-approval store is required; in-memory fallback is disabled.")
        self.pending_store = pending_store
        self.audit_log = audit_log
        self.execution_ledger = execution_ledger
        self.max_pending = max_pending

    def _require_audit(self) -> None:
        if self.audit_log is None or not callable(getattr(self.audit_log, "append", None)):
            raise RuntimeError("Approval audit sink is required; operation blocked.")

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
        self._require_audit()
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A trusted actor and session identity are required.")
        skill_id, arguments, action = self._extract_action(planner, result)
        stored = self.pending_store.put(
            skill_id, arguments, action, actor=actor, session_id=session_id
        )
        pending_id = stored.pending_id
        try:
            self._audit("requested", pending_id, actor, skill_id)
        except Exception as exc:
            try:
                self.pending_store.deny(pending_id)
            except Exception:
                pass
            raise RuntimeError(
                "Approval request could not be audited; no approval was issued."
            ) from exc
        return ApprovalOutcome(False, "pending_approval", pending_id=pending_id, result=action)

    def approve(self, planner, pending_id: str, *, actor: str, session_id: str,
                confirm: Callable[[dict], bool]) -> ApprovalOutcome:
        try:
            self._require_audit()
        except RuntimeError:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            return ApprovalOutcome(False, "identity_invalid", pending_id=pending_id)
        pending = self._consume(pending_id, actor=actor, session_id=session_id)
        if pending is None:
            return ApprovalOutcome(False, "pending_not_found", pending_id=pending_id)
        action = pending["action"]
        import json
        display_action = json.loads(json.dumps(action))
        try:
            confirmed = callable(confirm) and confirm(display_action) is True
        except Exception:
            confirmed = False
        if not confirmed:
            try:
                self._audit("denied", pending_id, actor, pending["skill_id"])
            except Exception:
                return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)
            return ApprovalOutcome(False, "approval_denied", pending_id=pending_id)

        try:
            self._audit("approved", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)

        # When configured, durably claim the action before invoking any side effect.
        # Existing non-prepared records must be reconciled, never replayed blindly.
        if self.execution_ledger is not None:
            try:
                execution, created = self.execution_ledger.begin(pending_id, action)
                if not created:
                    # Any pre-existing record means this approval key has already
                    # entered the durable execution lifecycle. Even "prepared" is
                    # ambiguous across process crashes; fail closed and reconcile
                    # instead of allowing a second coordinator to start the action.
                    try:
                        self._audit(
                            "execution_recovery_required", pending_id, actor,
                            pending["skill_id"],
                        )
                    except Exception:
                        pass
                    return ApprovalOutcome(
                        False, "recovery_required", pending_id=pending_id,
                        result={"execution_state": execution.state},
                    )
                self.execution_ledger.transition(pending_id, "running")
            except Exception as exc:
                return ApprovalOutcome(
                    False, "execution_ledger_unavailable", pending_id=pending_id,
                    result=type(exc).__name__,
                )

        try:
            result = (
                planner.agent.execute_approved_action(action)
                if hasattr(planner, "agent") and hasattr(planner.agent, "execute_approved_action")
                else planner.execute_approved_action(action)
            )
        except Exception as exc:
            if self.execution_ledger is not None:
                try:
                    self.execution_ledger.transition(
                        pending_id, "uncertain",
                        result={"reason": "executor raised", "error_type": type(exc).__name__},
                    )
                except Exception:
                    pass
            if self.execution_ledger is not None:
                try:
                    self._audit("execution_uncertain", pending_id, actor, pending["skill_id"])
                except Exception:
                    pass
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"execution_error": type(exc).__name__, "execution_state": "uncertain"},
                )
            try:
                self._audit("execution_failed", pending_id, actor, pending["skill_id"])
            except Exception:
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"execution_error": type(exc).__name__, "audit": "unavailable"},
                )
            return ApprovalOutcome(False, "execution_failed", pending_id=pending_id, result=type(exc).__name__)

        # A returned result can represent an application-level failure without
        # raising. Never emit "executed"/"completed" for an explicit ok=False.
        # Adapters may return either typed result objects or JSON-style dicts.
        # Treat an explicit false value consistently; otherwise a dict such as
        # {"ok": False} would be incorrectly reported as successful execution.
        result_ok = result.get("ok", True) if isinstance(result, dict) else getattr(result, "ok", True)
        if result_ok is False:
            if self.execution_ledger is not None:
                try:
                    self.execution_ledger.transition(
                        pending_id, "failed", result={"ok": False, "result_type": type(result).__name__}
                    )
                except Exception as exc:
                    return ApprovalOutcome(
                        False, "recovery_required", pending_id=pending_id,
                        result={"execution_result": result, "ledger_error": type(exc).__name__},
                    )
            try:
                self._audit("execution_failed", pending_id, actor, pending["skill_id"])
            except Exception:
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"execution_result": result, "audit": "unavailable"},
                )
            return ApprovalOutcome(False, "execution_failed", pending_id=pending_id, result=result)

        if self.execution_ledger is not None:
            try:
                self.execution_ledger.transition(
                    pending_id, "completed",
                    result={"ok": True, "result_type": type(result).__name__},
                )
            except Exception as exc:
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"execution_result": result, "ledger_error": type(exc).__name__},
                )

        try:
            self._audit("executed", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(
                False, "recovery_required", pending_id=pending_id,
                result={"execution_result": result, "audit": "unavailable"},
            )
        return ApprovalOutcome(True, "completed", pending_id=pending_id, result=result)

    def _consume(self, pending_id, *, actor, session_id):
        item = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
        if item is None:
            return None
        return {"skill_id": item.skill_id, "arguments": item.arguments,
                "action": item.action, "actor": actor, "session_id": session_id}

    def _audit(self, event_type, pending_id, actor, skill_id):
        self.audit_log.append(event_type=event_type, pending_id=pending_id,
                              actor=actor, skill_id=skill_id)
