"""Bounded, permission-gated tool loop; never exposes arbitrary shell execution."""
from dataclasses import dataclass
import hashlib
from typing import Iterable, Optional

from companion import DEFAULT_PERSONA, EmotionState
from core.permissions import PermissionGate
from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill
from skills import FileSkill, Skill, SystemSkill, TimeSkill
from skills.registry import SkillRegistry
from skills.catalog import SkillCatalog


@dataclass(frozen=True)
class AgentResponse:
    ok: bool
    message: str
    tool_name: Optional[str] = None
    data: object = None
    stage: str = "report"


class YukiCoreAgent:
    """One intent -> registered skill -> explicit approval -> observed result."""

    def __init__(self, skills: Optional[Iterable[Skill]] = None,
                 permission_gate: Optional[PermissionGate] = None,
                 registry: Optional[SkillRegistry] = None,
                 approval_coordinator=None):
        if registry is not None and skills is not None:
            raise ValueError("Pass either skills or registry, not both.")
        if registry is not None:
            self.registry = registry
        else:
            available = list(skills) if skills is not None else [TimeSkill(), SystemSkill(), FileSkill()]
            self.registry = SkillRegistry(available)
        self.permission_gate = permission_gate or PermissionGate()
        self.approval_coordinator = approval_coordinator
        self.invocation_approvals = OneShotSkillApprovals()
        self.persona = DEFAULT_PERSONA
        self.emotion = EmotionState()
        self.max_steps = 3

    @property
    def skills(self):
        return {name: self.registry.get(name) for name in self.registry.names()}

    def available_skills(self):
        return self.registry.describe()

    def discover_skills(self, task_query: str, *, limit: int = 5):
        """Find relevant registered skills without executing or authorizing them."""
        return SkillCatalog(self.registry).search(task_query, limit=limit)

    def run_skill_for_task(self, task_query: str, argument: str = "") -> AgentResponse:
        """Select the best catalog match, then use the normal approval-gated path.

        Empty queries and missing matches fail closed. Catalog ranking is discovery,
        not authorization; PermissionGate remains the final approval boundary.
        """
        cards = self.discover_skills(task_query, limit=5)
        if not cards:
            return AgentResponse(
                False,
                "No registered skill matches this task; clarify the request or choose a skill.",
                stage="skill_not_found",
            )
        selected = cards[0]
        return self.run_skill(selected.name, argument)

    def list_approval_recovery(self, *, limit: int = 100) -> list[dict[str, str]]:
        """List interrupted approval records without exposing bearer receipt IDs."""
        coordinator = self.approval_coordinator
        store = getattr(coordinator, "pending_store", None)
        if store is None:
            return []
        records = store.list_recovery_required(limit=limit)
        return [
            {
                "reference": str(item.get("pending_id", ""))[-12:],
                "skill_id": str(item.get("skill_id", "")),
                "status": str(item.get("status", "")),
                "updated_at": str(item.get("updated_at", "")),
            }
            for item in records
        ]

    def resolve_approval_recovery(self, reference: str, decision: str) -> bool:
        """Record an operator decision; this method never retries the approved action."""
        coordinator = self.approval_coordinator
        store = getattr(coordinator, "pending_store", None)
        audit = getattr(coordinator, "audit_log", None)
        if store is None or audit is None:
            return False
        if decision not in {"completed", "failed", "uncertain"}:
            return False
        record = store.find_recovery_by_reference(reference)
        if record is None:
            return False
        actor = self.permission_gate.actor_id
        session_id = self.permission_gate.session_id
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            return False
        confirmed = self.permission_gate.confirm_durable_action(
            action=f"approval-recovery:{reference}:{decision}",
            reason=(
                f"Operator reconciliation only; this does not execute or retry the skill. "
                f"Skill: {record['skill_id']}; current state: {record['status']}; "
                f"decision: {decision}. "
                "Confirm only after independently verifying external side effects."
            ),
            risk="high",
        )
        if not confirmed:
            return False
        event = {
            "completed": "reconciliation_completed",
            "failed": "reconciliation_failed",
            "uncertain": "reconciliation_uncertain",
        }[decision]
        try:
            audit.append(
                event_type=event,
                pending_id=record["pending_id"],
                actor=actor,
                session_id=session_id,
                skill_id=record["skill_id"],
                idempotency_key=hashlib.sha256(
                    f"approval-reconciliation:{record['pending_id']}:{decision}".encode("utf-8")
                ).hexdigest(),
            )
        except Exception:
            return False
        if decision == "uncertain":
            # Keep it visible in the recovery queue; uncertainty is not resolution.
            return True
        terminal = "reconciled_completed" if decision == "completed" else "reconciled_failed"
        try:
            return store.transition(
                record["pending_id"],
                expected=record["status"],
                new_status=terminal,
            )
        except Exception:
            # The append-only audit records the operator's attempted decision;
            # the still-visible recovery row must be inspected again.
            return False

    def _execute_registered_skill(self, name: str, argument: str) -> AgentResponse:
        """Execute a previously approved registered skill without requesting approval again."""
        skill = self.registry.get(name)
        if skill is None:
            return AgentResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        risk = str(getattr(skill, "risk", "high")).lower()
        needs_guard_approval = (
            bool(getattr(skill, "requires_approval", True))
            or risk not in {"low", "medium", "high", "critical"}
            or risk in {"high", "critical"}
        )
        if needs_guard_approval:
            self.invocation_approvals.approve(name, argument)
        result = invoke_registered_skill(
            self.registry,
            name,
            argument,
            approvals=self.invocation_approvals,
            expected_skill=skill,
        )
        return AgentResponse(
            result.ok, result.message, name, result.data,
            stage="report" if result.ok else "observe",
        )

    def execute_approved_action(self, action: dict) -> AgentResponse:
        """Execute only a stored, explicitly approved registered-skill snapshot."""
        if not isinstance(action, dict) or set(action) != {"skill_id", "arguments"}:
            return AgentResponse(False, "Blocked: invalid approved action snapshot.", stage="blocked")
        name = action.get("skill_id")
        arguments = action.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict) or set(arguments) != {"argument"}:
            return AgentResponse(False, "Blocked: invalid approved skill arguments.", stage="invalid_argument")
        argument = arguments.get("argument")
        if not isinstance(argument, str) or len(argument) > 20000:
            return AgentResponse(False, "Blocked: approved skill argument is invalid.", name, stage="invalid_argument")
        try:
            argument.encode("utf-8")
        except UnicodeEncodeError:
            return AgentResponse(False, "Blocked: approved skill argument is invalid UTF-8.", name, stage="invalid_argument")
        if self.registry.get(name) is None:
            return AgentResponse(False, "Blocked: approved skill is no longer registered.", name, stage="blocked")
        return self._execute_registered_skill(name, argument)

    def _run_durable_approval(self, skill, argument: str) -> AgentResponse:
        coordinator = self.approval_coordinator
        action = {"skill_id": skill.name, "arguments": {"argument": argument}}
        try:
            pending = coordinator.record(
                self, {"action": action},
                actor=self.permission_gate.actor_id,
                session_id=self.permission_gate.session_id,
            )
        except Exception:
            return AgentResponse(
                False, "Approval could not be durably recorded; the skill was not executed.",
                skill.name, stage="audit_unavailable",
            )

        def confirm(snapshot):
            current = self.registry.get(snapshot.get("skill_id"))
            if current is None or current.name != skill.name:
                return False
            supplied = snapshot.get("arguments", {}).get("argument")
            if not isinstance(supplied, str) or supplied != argument:
                return False
            fingerprint = hashlib.sha256(argument.encode("utf-8")).hexdigest()
            risk = str(getattr(current, "risk", "high")).lower()
            if risk not in {"low", "medium", "high"}:
                risk = "high"
            return self.permission_gate.confirm_durable_action(
                action=f"skill:{current.name}:sha256:{fingerprint}",
                reason=(
                    f"Invoke {current.name} ({current.description}); arguments are redacted. "
                    f"Argument SHA-256: {fingerprint}."
                ),
                risk=risk,
            )

        outcome = coordinator.approve(
            self, pending.pending_id,
            actor=self.permission_gate.actor_id,
            session_id=self.permission_gate.session_id,
            confirm=confirm,
        )
        if outcome.status == "completed" and isinstance(outcome.result, AgentResponse):
            return outcome.result
        stages = {
            "approval_denied": "permission_denied",
            "audit_unavailable": "audit_unavailable",
            "recovery_required": "recovery_required",
            "pending_not_found": "approval_unavailable",
        }
        return AgentResponse(
            False,
            "Not executed: approval was not completed." if outcome.status != "recovery_required"
            else "The skill may have run; its outcome requires manual recovery review.",
            skill.name, outcome.result, stage=stages.get(outcome.status, "approval_unavailable"),
        )

    def run_skill(self, name: str, argument: str = "") -> AgentResponse:
        if not isinstance(argument, str) or len(argument) > 20000:
            return AgentResponse(
                False,
                "Blocked: skill arguments must be text no longer than 20000 characters.",
                name if isinstance(name, str) else None,
                stage="invalid_argument",
            )
        try:
            argument_bytes = argument.encode("utf-8")
        except UnicodeEncodeError:
            return AgentResponse(
                False, "Blocked: skill arguments must be valid UTF-8 text.",
                name if isinstance(name, str) else None, stage="invalid_argument",
            )
        skill = self.registry.get(name)
        if skill is None:
            return AgentResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        if self.approval_coordinator is not None:
            return self._run_durable_approval(skill, argument)

        # Compatibility path for callers that have not configured durable approvals.
        argument_fingerprint = hashlib.sha256(argument_bytes).hexdigest()
        request_args = {
            "action": f"skill:{skill.name}:sha256:{argument_fingerprint}",
            "reason": (
                f"Invoke {skill.name} ({skill.description}); arguments are redacted. "
                f"Argument SHA-256: {argument_fingerprint}."
            ),
            "risk": skill.risk if skill.risk in {"low", "medium", "high"} else "high",
        }
        receipt_id = None
        request_with_receipt = getattr(self.permission_gate, "request_with_receipt", None)
        if callable(request_with_receipt):
            approved, receipt_id = request_with_receipt(
                **request_args, skill_id=skill.name
            )
        else:
            approved = self.permission_gate.request(**request_args)
        if not approved:
            return AgentResponse(False, "Not executed: explicit human approval was not granted.",
                                 name, stage="permission_denied")
        result = self._execute_registered_skill(name, argument)
        record_execution = getattr(self.permission_gate, "record_execution", None)
        if receipt_id is not None and callable(record_execution):
            audited = record_execution(
                receipt_id, skill_id=skill.name, succeeded=bool(result.ok)
            )
            if not audited:
                return AgentResponse(
                    False,
                    "The skill may have run, but its final audit event could not be saved. Verify the outcome before retrying.",
                    name, result.data, stage="recovery_required",
                )
        return result
