"""Central fail-closed policy for tool execution boundaries.

Callers must provide a synchronous approval callback that receives the tool name,
risk label, and validated arguments. A callback must return the literal True.
"""
from __future__ import annotations

from typing import Any, Callable

Risk = str
ApprovalCallback = Callable[[str, Risk, dict[str, Any]], bool]

_VALID_RISKS = frozenset({"low", "medium", "high", "critical"})


def approval_required(risk: Risk, *, auto_approve: bool = False) -> bool:
    """Return whether an action requires a human approval callback.

    Only explicitly auto-approved low-risk tools can bypass the callback.
    """
    if risk not in _VALID_RISKS:
        return True
    return risk != "low" or auto_approve is not True


def authorize_tool_call(
    name: str,
    risk: Risk,
    arguments: dict[str, Any],
    *,
    approval_callback: ApprovalCallback | None,
    auto_approve: bool = False,
) -> bool:
    """Authorize one already-validated tool invocation, failing closed on errors."""
    if not isinstance(name, str) or not name.strip() or risk not in _VALID_RISKS:
        return False
    if not isinstance(arguments, dict):
        return False
    if not approval_required(risk, auto_approve=auto_approve):
        return True
    if not callable(approval_callback):
        return False
    try:
        decision = approval_callback(name, risk, arguments)
        if decision is True:
            return True
        # Never leak an un-awaited coroutine/future from a synchronous gate.
        close = getattr(decision, "close", None)
        if callable(close):
            close()
        return False
    except Exception:
        return False
