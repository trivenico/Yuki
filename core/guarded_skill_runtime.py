"""Central approval gate for invoking registered Yuki skills.

This service is deliberately independent of the LLM planner. Callers must route
all skill execution through this boundary; it does not sandbox skill code.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from typing import Callable, Any


@dataclass(frozen=True)
class SkillInvocationResult:
    ok: bool
    message: str
    skill_name: str
    status: str
    data: Any = None


class OneShotSkillApprovals:
    """Thread-safe, process-local one-shot approvals bound to exact arguments."""

    def __init__(self) -> None:
        self._approved: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _key(skill_name: str, argument: str) -> tuple[str, str]:
        import hashlib
        if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
            raise ValueError("A non-empty bounded skill name is required.")
        if not isinstance(argument, str) or len(argument) > 20000:
            raise ValueError("Approval argument must be text no longer than 20000 characters.")
        try:
            encoded_argument = argument.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Approval argument must be valid UTF-8 text.") from exc
        digest = hashlib.sha256(encoded_argument).hexdigest()
        return skill_name, digest

    def approve(self, skill_name: str, argument: str = "") -> None:
        key = self._key(skill_name, argument)
        with self._lock:
            self._approved.add(key)

    def consume(self, skill_name: str, argument: str = "") -> bool:
        try:
            key = self._key(skill_name, argument)
        except ValueError:
            return False
        with self._lock:
            if key not in self._approved:
                return False
            self._approved.remove(key)
            return True

    def clear(self) -> None:
        with self._lock:
            self._approved.clear()


_INVALID_RESULT_DATA = object()


def _bounded_result_data(value: Any, limit: int) -> tuple[Any, bool]:
    """Return bounded JSON-safe data; reject the whole payload if any value is unsupported."""
    budget = [limit]
    truncated = [False]

    def visit(item: Any, depth: int = 0) -> Any:
        if budget[0] <= 0 or depth > 8:
            truncated[0] = True
            return None
        if item is None or isinstance(item, bool):
            budget[0] -= 4
            return item
        if isinstance(item, int):
            text = str(item)
            if len(text) > budget[0]:
                truncated[0] = True
                return None
            budget[0] -= len(text)
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                return _INVALID_RESULT_DATA
            text = repr(item)
            if len(text) > budget[0]:
                truncated[0] = True
                return None
            budget[0] -= len(text)
            return item
        if isinstance(item, str):
            if len(item) > budget[0]:
                result = item[:max(0, budget[0])]
                budget[0] = 0
                truncated[0] = True
                return result
            budget[0] -= len(item)
            return item
        if isinstance(item, dict):
            result = {}
            for index, (key, child) in enumerate(item.items()):
                if index >= 100 or budget[0] <= 0:
                    truncated[0] = True
                    break
                if not isinstance(key, str):
                    return _INVALID_RESULT_DATA
                safe_key = visit(key, depth + 1)
                if safe_key is _INVALID_RESULT_DATA or not isinstance(safe_key, str):
                    return _INVALID_RESULT_DATA
                safe_value = visit(child, depth + 1)
                if safe_value is _INVALID_RESULT_DATA:
                    return _INVALID_RESULT_DATA
                result[safe_key] = safe_value
            if truncated[0] and budget[0] > 16:
                result["_truncated"] = True
                budget[0] -= 16
            return result
        if isinstance(item, (list, tuple)):
            result = []
            for index, child in enumerate(item):
                if index >= 100 or budget[0] <= 0:
                    truncated[0] = True
                    break
                safe_child = visit(child, depth + 1)
                if safe_child is _INVALID_RESULT_DATA:
                    return _INVALID_RESULT_DATA
                result.append(safe_child)
            if truncated[0] and budget[0] > 16:
                result.append({"_truncated": True})
                budget[0] -= 16
            return result
        return _INVALID_RESULT_DATA

    safe = visit(value)
    if safe is _INVALID_RESULT_DATA:
        return None, True
    return safe, truncated[0]


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
    try:
        argument.encode("utf-8")
    except UnicodeEncodeError:
        record("invalid_argument")
        return SkillInvocationResult(False, "Argument must be valid UTF-8 text.", skill_name, "invalid_argument")
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
    known_risks = {"low", "medium", "high", "critical"}
    needs_approval = (
        bool(getattr(skill, "requires_approval", True))
        or risk not in known_risks
        or risk in {"high", "critical"}
    )
    if needs_approval and not approvals.consume(skill_name, argument):
        record("approval_required")
        return SkillInvocationResult(False, "Explicit one-shot approval is required.", skill_name, "approval_required")

    execute = getattr(skill, "execute", None)
    if not callable(execute):
        execute = getattr(skill, "run", None)
    if not callable(execute):
        record("invalid_skill")
        return SkillInvocationResult(False, "Registered skill has no supported run()/execute() interface.", skill_name, "invalid_skill")

    try:
        result = execute(argument)
    except Exception as exc:
        record("execution_failed")
        return SkillInvocationResult(False, f"Skill invocation failed safely ({type(exc).__name__}).", skill_name, "execution_failed")

    data = None
    if isinstance(result, str):
        ok, message = True, result
    elif isinstance(result, bool):
        ok = result
        message = "Skill completed." if result else "Skill reported failure."
    elif isinstance(result, dict):
        ok = bool(result.get("ok", True))
        message = result.get("message", "Skill completed." if ok else "Skill failed.")
        data = {key: value for key, value in result.items() if key not in {"ok", "message"}}
    else:
        ok = bool(getattr(result, "ok", False))
        message = getattr(result, "message", "Skill returned an invalid result.")
        data = getattr(result, "data", None)
    if not isinstance(message, str):
        message = "Skill returned an invalid result."
        ok, data = False, None

    if data is not None:
        data, data_truncated = _bounded_result_data(data, max_result_chars)
        if data_truncated:
            record("result_data_truncated")
    status = "completed" if ok else "skill_failed"
    record(status)
    return SkillInvocationResult(ok, message[:max_result_chars], skill_name, status, data)
