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

    def record(self, planner, result, *, actor: str, session_id: str,
               idempotency_key: str | None = None):
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 256:
            raise ValueError("A bounded actor identity is required.")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
            raise ValueError("A bounded session identity is required.")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
            or len(idempotency_key) > 256
        ):
            raise ValueError("A bounded idempotency key is required.")
        skill_id, arguments, action = self._extract_action(planner, result)
        created = True
        if self.pending_store is not None:
            stored = self.pending_store.put(
                skill_id, arguments, action, actor=actor, session_id=session_id,
                idempotency_key=idempotency_key,
            )
            pending_id = stored.pending_id
            created = stored.created
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
        if not created:
            if getattr(stored, "status", "pending") != "pending":
                raise RuntimeError("idempotent approval request already reached a terminal or claimed state")
            return ApprovalOutcome(False, "pending_approval", pending_id=pending_id, result=action)
        try:
            self._audit("requested", pending_id, actor, skill_id)
        except Exception as exc:
            # Never leave an un-audited approval request available for later execution.
            if self.pending_store is not None:
                try:
                    self.pending_store.deny(pending_id, actor=actor, session_id=session_id)
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
        if (not isinstance(actor, str) or not actor.strip() or len(actor) > 256 or
                not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256):
            return ApprovalOutcome(
                False, "pending_not_found",
                pending_id=pending_id if isinstance(pending_id, str) else None,
            )
        if not callable(confirm):
            return ApprovalOutcome(
                False, "approval_confirmation_unavailable",
                pending_id=pending_id if isinstance(pending_id, str) else None,
            )
        pending = self._claim(pending_id, actor=actor, session_id=session_id)
        if pending is None:
            return ApprovalOutcome(False, "pending_not_found", pending_id=pending_id if isinstance(pending_id, str) else None)

        persistent = self.pending_store is not None
        state = pending.get("status", "claimed")
        if persistent and state == "executing":
            return ApprovalOutcome(
                False, "recovery_required", pending_id=pending_id,
                result={"outcome": "unknown", "reason": "execution_was_already_claimed"},
            )
        if persistent:
            if state in {"claimed_new", "claimed"}:
                # A prior process may have stopped while awaiting confirmation.
                # Re-present the same immutable action for fresh confirmation;
                # the atomic claimed -> approved transition still permits only
                # one caller to authorize execution.
                action = pending["action"]
                import json
                display_action = json.loads(json.dumps(action))
                try:
                    confirmed = confirm(display_action) is True
                except Exception:
                    confirmed = False
                if not confirmed:
                    transitioned = self.pending_store.transition(
                        pending_id, expected_status="claimed", new_status="denied",
                        actor=actor, session_id=session_id,
                    )
                    if transitioned is None:
                        return ApprovalOutcome(False, "recovery_required", pending_id=pending_id)
                    try:
                        self._audit("denied", pending_id, actor, pending["skill_id"])
                    except Exception:
                        return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)
                    return ApprovalOutcome(False, "approval_denied", pending_id=pending_id)
                approved = self.pending_store.transition(
                    pending_id, expected_status="claimed", new_status="approved",
                    actor=actor, session_id=session_id,
                )
                if approved is None:
                    return ApprovalOutcome(False, "recovery_required", pending_id=pending_id)
                pending["status"] = "approved"
            elif state == "execution_unavailable":
                resumed = self.pending_store.transition(
                    pending_id, expected_status="execution_unavailable",
                    new_status="approved", actor=actor, session_id=session_id,
                )
                if resumed is None:
                    return ApprovalOutcome(False, "recovery_required", pending_id=pending_id)
                pending["status"] = "approved"
            elif state != "approved":
                # An interrupted execution or terminal state must be reconciled;
                # never replay it automatically.
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"reason": "approval_requires_reconciliation", "state": state},
                )
            # An already-approved durable record resumes without asking for
            # confirmation again; the original confirmation is persisted.
        else:
            # The non-persistent compatibility path retains the existing
            # one-shot in-memory claim and confirmation semantics.
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

        action = pending["action"]

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
            if persistent:
                unavailable = self.pending_store.transition(
                    pending_id, expected_status="approved", new_status="execution_unavailable",
                    actor=actor, session_id=session_id,
                )
                if unavailable is None:
                    return ApprovalOutcome(False, "recovery_required", pending_id=pending_id)
            return ApprovalOutcome(False, "execution_unavailable", pending_id=pending_id)

        try:
            self._audit("approved", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)

        # Persist the start marker before changing the durable state to
        # executing. If the audit write fails, the executor is never called and
        # the approval remains safely retryable.
        try:
            self._audit("execution_started", pending_id, actor, pending["skill_id"])
        except Exception:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)

        if persistent:
            executing = self.pending_store.transition(
                pending_id, expected_status="approved", new_status="executing",
                actor=actor, session_id=session_id,
            )
            if executing is None:
                return ApprovalOutcome(False, "recovery_required", pending_id=pending_id)

        try:
            result = target(action)
        except Exception as exc:
            # An executor exception does not prove that no external side effect
            # occurred: a provider may have committed the action before a
            # timeout, transport reset, or response-decoding failure.
            try:
                self._audit("outcome_unknown", pending_id, actor, pending["skill_id"])
            except Exception:
                audit_error = "unavailable"
            else:
                audit_error = None
            if persistent:
                self.pending_store.transition(
                    pending_id, expected_status="executing", new_status="outcome_unknown",
                    actor=actor, session_id=session_id,
                )
            return ApprovalOutcome(
                False, "recovery_required", pending_id=pending_id,
                result={"execution_error": type(exc).__name__, "outcome": "unknown",
                        **({"audit": audit_error} if audit_error else {})},
            )

        try:
            self._audit("executed", pending_id, actor, pending["skill_id"])
        except Exception:
            if persistent:
                self.pending_store.transition(
                    pending_id, expected_status="executing", new_status="completed",
                    actor=actor, session_id=session_id,
                )
            return ApprovalOutcome(
                False, "recovery_required", pending_id=pending_id,
                result={"execution_result": result, "audit": "unavailable"},
            )
        if persistent:
            completed = self.pending_store.transition(
                pending_id, expected_status="executing", new_status="completed",
                actor=actor, session_id=session_id,
            )
            if completed is None:
                return ApprovalOutcome(
                    False, "recovery_required", pending_id=pending_id,
                    result={"execution_result": result, "state_persistence": "unavailable"},
                )
        return ApprovalOutcome(bool(getattr(result, "ok", True)), "completed", pending_id=pending_id, result=result)

    def reconcile(self, pending_id: str, *, actor: str, session_id: str,
                  resolution: str, evidence_reference: str) -> ApprovalOutcome:
        """Record an explicit owner-attested resolution without replaying work."""
        if (self.pending_store is None or
                not isinstance(actor, str) or not actor.strip() or len(actor) > 256 or
                not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256 or
                not isinstance(resolution, str) or resolution not in {"completed", "not_executed"} or
                not isinstance(evidence_reference, str) or not evidence_reference.strip() or
                len(evidence_reference) > 512):
            return ApprovalOutcome(False, "invalid_reconciliation", pending_id=pending_id)
        current = self.pending_store.get_for_identity(
            pending_id, actor=actor, session_id=session_id,
        )
        if current is None or current.status not in {"executing", "outcome_unknown"}:
            return ApprovalOutcome(False, "reconciliation_not_available", pending_id=pending_id)
        event = "reconciled_executed" if resolution == "completed" else "reconciled_not_executed"
        try:
            self._audit(event, pending_id, actor, current.skill_id)
        except Exception:
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)
        reconciled = self.pending_store.reconcile(
            pending_id, actor=actor, session_id=session_id,
            resolution=resolution, evidence_reference=evidence_reference,
        )
        if reconciled is None:
            return ApprovalOutcome(False, "reconciliation_conflict", pending_id=pending_id)
        return ApprovalOutcome(True, "reconciled_" + resolution, pending_id=pending_id)

    def deny(self, pending_id: str, *, actor: str, session_id: str) -> ApprovalOutcome:
        """Deny a still-pending owner/session-bound request exactly once."""
        if (self.pending_store is None or
                not isinstance(actor, str) or not actor.strip() or len(actor) > 256 or
                not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256):
            return ApprovalOutcome(False, "pending_not_found",
                                   pending_id=pending_id if isinstance(pending_id, str) else None)
        pending = self.pending_store.transition(
            pending_id, expected_status="pending", new_status="denied",
            actor=actor, session_id=session_id,
        )
        if pending is None:
            return ApprovalOutcome(False, "pending_not_found",
                                   pending_id=pending_id if isinstance(pending_id, str) else None)
        try:
            self._audit("denied", pending_id, actor, pending.skill_id)
        except Exception:
            # Denied state is durable even if the audit sink is unavailable;
            # never restore a denied action to an executable state.
            return ApprovalOutcome(False, "audit_unavailable", pending_id=pending_id)
        return ApprovalOutcome(True, "approval_denied", pending_id=pending_id)

    def _claim(self, pending_id, *, actor, session_id):
        if not isinstance(pending_id, str) or not pending_id:
            return None
        if not isinstance(actor, str) or not isinstance(session_id, str):
            return None
        if self.pending_store is not None:
            item = self.pending_store.transition(
                pending_id, expected_status="pending", new_status="claimed",
                actor=actor, session_id=session_id,
            )
            newly_claimed = item is not None
            if item is None:
                identity_get = getattr(self.pending_store, "get_for_identity", None)
                if not callable(identity_get):
                    return None
                item = identity_get(pending_id, actor=actor, session_id=session_id)
                if (item is None or
                        item.status not in {"approved", "executing", "claimed", "execution_unavailable", "completed", "outcome_unknown"}):
                    return None
            return {"skill_id": item.skill_id, "arguments": item.arguments,
                    "action": item.action, "actor": actor, "session_id": session_id,
                    "status": "claimed_new" if newly_claimed else item.status}
        # Check identity and remove the approval under one lock so two concurrent
        # callers cannot both observe and execute the same in-memory action.
        with self._pending_lock:
            item = self._pending.get(pending_id)
            if item is None or item["actor"] != actor or item["session_id"] != session_id:
                return None
            return self._pending.pop(pending_id)

    def _audit(self, event_type, pending_id, actor, skill_id):
        self.audit_log.append(event_type=event_type, pending_id=pending_id,
                              actor=actor, skill_id=skill_id)
