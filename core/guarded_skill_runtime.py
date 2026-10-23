"""Central approval gate for invoking registered Yuki skills.

This service is deliberately independent of the LLM planner. Callers must route
all skill execution through this boundary; it does not sandbox skill code.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable, Any


@dataclass(frozen=True)
class SkillInvocationResult:
    ok: bool
    message: str
    skill_name: str
    status: str


class OneShotSkillApprovals:
    """Thread-safe, process-local one-shot approvals bound to a skill name."""

    def __init__(self) -> None:
        self._approved: set[str] = set()
        self._lock = threading.Lock()

    def approve(self, skill_name: str) -> None:
        if not isinstance(skill_name, str) or not skill_name:
            raise ValueError("A non-empty skill name is required.")
        with self._lock:
            self._approved.add(skill_name)

    def consume(self, skill_name: str) -> bool:
        with self._lock:
            if skill_name not in self._approved:
                return False
            self._approved.remove(skill_name)
            return True

    def clear(self) -> None:
        with self._lock:
            self._approved.clear()


def invoke_registered_skill(
    registry: Any,
    skill_name: str,
    argument: str = "",
    *,
    approvals: OneShotSkillApprovals,
    audit: Callable[[str, str], None] | None = None,
    max_result_chars: int = 2000,
) -> SkillInvocationResult:
    """Invoke one registry member after checking its risk and one-shot approval."""
    def record(status: str) -> None:
        if audit is not None:
            try:
                # Never include arguments, results, or memory content in audit data.
                audit("skill:invoke", status)
            except Exception:
                # Audit sink failure must not change authorization semantics.
                pass

    if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
        record("invalid_name")
        return SkillInvocationResult(False, "Invalid skill name.", str(skill_name)[:128], "invalid_name")
    if not isinstance(argument, str) or len(argument) > 20000:
        record("invalid_argument")
        return SkillInvocationResult(False, "Argument must be text no longer than 20000 characters.", skill_name, "invalid_argument")
    if isinstance(max_result_chars, bool) or not isinstance(max_result_chars, int) or not 1 <= max_result_chars <= 10000:
        max_result_chars = 2000

    try:
        skill = registry.get(skill_name)
    except Exception:
        record("registry_failed")
        return SkillInvocationResult(False, "Skill registry is unavailable.", skill_name, "registry_failed")
    if skill is None:
        record("not_found")
        return SkillInvocationResult(False, "Skill is not registered.", skill_name, "not_found")

    risk = str(getattr(skill, "risk", "high")).lower()
    needs_approval = bool(getattr(skill, "requires_approval", True)) or risk in {"high", "critical"}
    if needs_approval and not approvals.consume(skill_name):
        record("approval_required")
        return SkillInvocationResult(False, "Explicit one-shot approval is required.", skill_name, "approval_required")

    execute = getattr(skill, "execute", None)
    if not callable(execute):
        record("invalid_skill")
        return SkillInvocationResult(False, "Registered skill has no execute() interface.", skill_name, "invalid_skill")

    try:
        result = execute(argument)
    except Exception as exc:
        record("execution_failed")
        return SkillInvocationResult(False, f"Skill invocation failed safely ({type(exc).__name__}).", skill_name, "execution_failed")

    ok = bool(getattr(result, "ok", False))
    message = getattr(result, "message", "Skill returned an invalid result.")
    if not isinstance(message, str):
        message = "Skill returned an invalid result."
        ok = False
    status = "completed" if ok else "skill_failed"
    record(status)
    return SkillInvocationResult(ok, message[:max_result_chars], skill_name, status)
