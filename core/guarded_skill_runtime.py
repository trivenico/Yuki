"""Central approval gate for invoking registered Yuki skills.

This service is deliberately independent of the LLM planner. Callers must route
all skill execution through this boundary; it does not sandbox skill code.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from typing import Callable, Any


def _callable_code_identity(target: Any) -> Any:
    """Return the Python code object for a function, method, or callable object.

    This detects some in-place code replacement. It is not a sandbox and does
    not make mutable globals, closures, defaults, or instance state immutable.
    """
    if target is None:
        return None
    function = getattr(target, "__func__", target)
    code = getattr(function, "__code__", None)
    if code is not None:
        return code
    call_method = getattr(type(target), "__call__", None)
    call_function = getattr(call_method, "__func__", call_method)
    return getattr(call_function, "__code__", None)


@dataclass(frozen=True)
class SkillInvocationResult:
    ok: bool
    message: str
    skill_name: str
    status: str
    data: Any = None


class OneShotSkillApprovals:
    """Thread-safe, process-local one-shot approvals bound to exact arguments."""

    DEFAULT_MAX_PENDING = 4096

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < 1:
            raise ValueError("max_pending must be a positive integer.")
        self._max_pending = max_pending
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
            if key not in self._approved and len(self._approved) >= self._max_pending:
                raise RuntimeError("Pending skill approvals limit reached.")
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
_OMIT_RESULT_DATA = object()


def _json_string_content_cost(value: str, max_cost: int) -> tuple[int, int]:
    """Return escaped JSON string-content cost and the safe prefix length."""
    cost = 0
    length = 0
    for char in value:
        codepoint = ord(char)
        if char == '"' or codepoint == 92:
            char_cost = 2
        elif codepoint < 0x20 or 0xD800 <= codepoint <= 0xDFFF:
            char_cost = 6
        elif codepoint > 0xFFFF:
            char_cost = 12
        elif codepoint > 0x7E:
            char_cost = 6
        else:
            char_cost = 1
        if cost + char_cost > max_cost:
            break
        cost += char_cost
        length += 1
    return cost, length


def _bounded_result_data(value: Any, limit: int) -> tuple[Any, bool]:
    """Return JSON-safe result data whose compact JSON encoding fits limit."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 2:
        return None, value is not None

    budget = [limit]
    truncated = [False]

    def visit(item: Any, depth: int = 0) -> Any:
        if depth > 8 or budget[0] <= 0:
            truncated[0] = True
            return _OMIT_RESULT_DATA

        if item is None:
            cost = 4
            if cost > budget[0]:
                truncated[0] = True
                return _OMIT_RESULT_DATA
            budget[0] -= cost
            return None
        if isinstance(item, bool):
            cost = 4 if item else 5
            if cost > budget[0]:
                truncated[0] = True
                return _OMIT_RESULT_DATA
            budget[0] -= cost
            return item
        if isinstance(item, int):
            cost = len(str(item))
            if cost > budget[0]:
                truncated[0] = True
                return _OMIT_RESULT_DATA
            budget[0] -= cost
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                return _INVALID_RESULT_DATA
            cost = len(repr(item))
            if cost > budget[0]:
                truncated[0] = True
                return _OMIT_RESULT_DATA
            budget[0] -= cost
            return item
        if isinstance(item, str):
            available = max(0, budget[0] - 2)
            content_cost, prefix_length = _json_string_content_cost(item, available)
            if prefix_length < len(item):
                truncated[0] = True
                item = item[:prefix_length]
            cost = 2 + content_cost
            if cost > budget[0]:
                truncated[0] = True
                return _OMIT_RESULT_DATA
            budget[0] -= cost
            return item
        if isinstance(item, dict):
            if budget[0] < 2:
                truncated[0] = True
                return _OMIT_RESULT_DATA
            budget[0] -= 2
            result = {}
            for key, child in item.items():
                if len(result) >= 100:
                    truncated[0] = True
                    break
                if not isinstance(key, str):
                    return _INVALID_RESULT_DATA
                key_content_cost, key_length = _json_string_content_cost(key, max(0, budget[0] - 4))
                if key_length < len(key):
                    truncated[0] = True
                    break
                key_cost = 2 + key_content_cost
                separator_cost = 1 if result else 0
                if separator_cost + key_cost + 1 > budget[0]:
                    truncated[0] = True
                    break
                budget[0] -= separator_cost + key_cost + 1
                safe_value = visit(child, depth + 1)
                if safe_value is _INVALID_RESULT_DATA:
                    return _INVALID_RESULT_DATA
                if safe_value is _OMIT_RESULT_DATA:
                    truncated[0] = True
                    break
                result[key] = safe_value
            return result
        if isinstance(item, (list, tuple)):
            if budget[0] < 2:
                truncated[0] = True
                return _OMIT_RESULT_DATA
            budget[0] -= 2
            result = []
            for child in item:
                if len(result) >= 100:
                    truncated[0] = True
                    break
                separator_cost = 1 if result else 0
                if separator_cost > budget[0]:
                    truncated[0] = True
                    break
                budget[0] -= separator_cost
                safe_child = visit(child, depth + 1)
                if safe_child is _INVALID_RESULT_DATA:
                    return _INVALID_RESULT_DATA
                if safe_child is _OMIT_RESULT_DATA:
                    truncated[0] = True
                    break
                result.append(safe_child)
            return result
        return _INVALID_RESULT_DATA

    safe = visit(value)
    if safe is _INVALID_RESULT_DATA or safe is _OMIT_RESULT_DATA:
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
    expected_skill: Any = None,
    expected_execution: tuple[Any, Any, str, bool, Any] | None = None,
) -> SkillInvocationResult:
    """Invoke one registry member after checking its risk and one-shot approval.

    When expected_skill is supplied, execute only that exact registered object;
    a registry replacement between permission approval and dispatch fails closed.
    """
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
    if expected_skill is not None and skill is not expected_skill:
        # Burn any matching one-shot approval on an identity mismatch so a
        # transient registry swap cannot leave a reusable authorization behind.
        approvals.consume(skill_name, argument)
        record("skill_identity_changed")
        return SkillInvocationResult(
            False, "Registered skill changed after approval; invocation was blocked.",
            skill_name, "skill_identity_changed"
        )

    current_risk = str(getattr(skill, "risk", "high")).lower()
    current_requires_approval = bool(getattr(skill, "requires_approval", True))
    current_execute = getattr(skill, "execute", None)
    if not callable(current_execute):
        current_execute = getattr(skill, "run", None)

    if expected_execution is not None:
        approved_skill, approved_execute, approved_risk, approved_requires_approval, approved_code = expected_execution
        same_callable = (
            getattr(current_execute, "__self__", None) is getattr(approved_execute, "__self__", None)
            and getattr(current_execute, "__func__", None) is getattr(approved_execute, "__func__", None)
            if callable(current_execute) and callable(approved_execute)
            and (hasattr(current_execute, "__self__") or hasattr(approved_execute, "__self__"))
            else current_execute is approved_execute
        )
        current_code = _callable_code_identity(current_execute)
        approved_code_at_dispatch = _callable_code_identity(approved_execute)
        if (
            skill is not approved_skill
            or current_code is not approved_code
            or approved_code_at_dispatch is not approved_code
            or current_risk != approved_risk
            or current_requires_approval != approved_requires_approval
            or not callable(approved_execute)
            or not same_callable
        ):
            approvals.consume(skill_name, argument)
            record("skill_execution_changed")
            return SkillInvocationResult(
                False,
                "Skill execution target or authorization metadata changed after approval.",
                skill_name,
                "skill_execution_changed",
            )
        # Authorization and dispatch use the reviewed snapshot. Never re-resolve
        # a callable after this check; that would reintroduce a check/use gap.
        risk = approved_risk
        requires_approval = approved_requires_approval
        execute = approved_execute
    else:
        risk = current_risk
        requires_approval = current_requires_approval
        execute = current_execute

    known_risks = {"low", "medium", "high", "critical"}
    needs_approval = (
        requires_approval
        or risk not in known_risks
        or risk in {"high", "critical"}
    )
    if needs_approval and not approvals.consume(skill_name, argument):
        record("approval_required")
        return SkillInvocationResult(False, "Explicit one-shot approval is required.", skill_name, "approval_required")

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
