"""Skill adapter that routes CLI/task skill calls through durable approval and execution state."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DurableSkillResponse:
    ok: bool
    message: str
    tool_name: str | None = None
    data: object = None
    stage: str = "report"


class DurableSkillRuntime:
    """Expose the skill-agent interface while requiring durable approval per call."""

    def __init__(self, *, registry, coordinator, actor: str, confirm: Callable[[dict], bool],
                 session_id: str | None = None):
        if not callable(getattr(registry, "get", None)) or not callable(getattr(registry, "describe", None)):
            raise TypeError("A skill registry is required.")
        if not callable(getattr(coordinator, "record", None)) or not callable(getattr(coordinator, "approve", None)):
            raise TypeError("A durable approval coordinator is required.")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("A trusted actor is required.")
        if not callable(confirm):
            raise TypeError("A confirmation callback is required.")
        self.registry = registry
        self.coordinator = coordinator
        self.actor = actor
        self.confirm = confirm
        self.session_id = session_id or secrets.token_urlsafe(24)

    def available_skills(self):
        return self.registry.describe()

    def run_skill(self, name: str, argument: str = "") -> DurableSkillResponse:
        if not isinstance(name, str) or not name.strip():
            return DurableSkillResponse(False, "Blocked: skill name is invalid.", stage="invalid_input")
        if not isinstance(argument, str) or len(argument) > 500 or "\x00" in argument:
            return DurableSkillResponse(False, "Blocked: skill argument must be a NUL-free string of at most 500 characters.",
                                        name, stage="invalid_input")
        skill = self.registry.get(name)
        if skill is None:
            return DurableSkillResponse(False, "Blocked: that skill is not registered.", name, stage="blocked")
        proposed = {"action": {"skill_id": skill.name, "arguments": {"argument": argument}}}
        try:
            pending = self.coordinator.record(None, proposed, actor=self.actor, session_id=self.session_id)
            outcome = self.coordinator.approve(
                self, pending.pending_id, actor=self.actor, session_id=self.session_id,
                confirm=self.confirm,
            )
        except Exception:
            return DurableSkillResponse(False, "Skill request failed safely; no successful execution was confirmed.",
                                        name, stage="recovery_required")
        if outcome.status != "completed":
            return DurableSkillResponse(
                False, f"Skill not completed ({outcome.status}).", name,
                data=outcome.result, stage=outcome.status,
            )
        result = outcome.result
        if isinstance(result, dict):
            ok = result.get("ok", True)
            message = str(result.get("message", "Skill completed." if ok else "Skill failed."))
            data = {key: value for key, value in result.items() if key not in {"ok", "message"}}
        else:
            ok = getattr(result, "ok", True)
            message = str(getattr(result, "message", "Skill completed."))
            data = getattr(result, "data", None)
        return DurableSkillResponse(bool(ok), message, name, data, "report" if ok else "observe")

    def execute_approved_action(self, action: dict):
        """Coordinator-only executor; action must match the stored approved snapshot."""
        if not isinstance(action, dict) or set(action) < {"skill_id", "arguments"}:
            raise ValueError("Approved action is malformed.")
        skill_id = action.get("skill_id")
        arguments = action.get("arguments")
        if not isinstance(skill_id, str) or not isinstance(arguments, dict):
            raise ValueError("Approved action is malformed.")
        argument = arguments.get("argument", "")
        if not isinstance(argument, str) or len(argument) > 500 or "\x00" in argument:
            raise ValueError("Approved skill argument is invalid.")
        skill = self.registry.get(skill_id)
        if skill is None:
            raise ValueError("Approved skill is no longer registered.")
        result = skill.execute(argument)
        if isinstance(result, dict):
            # A missing or non-boolean status must never be interpreted as success.
            # In particular, bool("false") is True in Python.
            ok = result.get("ok")
            if type(ok) is not bool:
                return {
                    "ok": False,
                    "message": "Skill returned an invalid result contract; success must be an explicit boolean.",
                    "error": "invalid_result_contract",
                }
            return result
        ok = getattr(result, "ok", None)
        if type(ok) is not bool:
            return {
                "ok": False,
                "message": "Skill returned an invalid result contract; success must be an explicit boolean.",
                "error": "invalid_result_contract",
            }
        return {
            "ok": ok,
            "message": str(getattr(result, "message", "Skill completed." if ok else "Skill failed.")),
            "data": getattr(result, "data", None),
        }
