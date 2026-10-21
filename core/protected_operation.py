"""Enforce release-gate decisions before protected side effects.

This module is intentionally small and dependency-injected. It does not choose which
operations are protected; callers must route every sensitive operation through this
wrapper and must not retain an alternate ungated path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from core.release_gate import ReleaseGateDecision

T = TypeVar("T")


@dataclass(frozen=True)
class ProtectedOperationResult:
    allowed: bool
    reason: str
    gate_decision: ReleaseGateDecision | None
    operation_result: Any = None


def run_protected_operation(
    *,
    evaluate_gate: Callable[[], ReleaseGateDecision],
    operation: Callable[[], T],
) -> ProtectedOperationResult:
    """Run an operation only after an explicit, persisted allow decision.

    Fail closed on exceptions or malformed gate output. Exceptions from the protected
    operation itself are not hidden; once an allowed gate has passed, operation failure
    belongs to the operation's caller and recovery policy.
    """
    try:
        decision = evaluate_gate()
    except Exception:
        return ProtectedOperationResult(
            allowed=False,
            reason="release_gate_evaluation_failed",
            gate_decision=None,
        )

    if not isinstance(decision, ReleaseGateDecision):
        return ProtectedOperationResult(
            allowed=False,
            reason="release_gate_returned_invalid_decision",
            gate_decision=None,
        )
    if decision.allowed is not True or decision.audit_persisted is not True:
        return ProtectedOperationResult(
            allowed=False,
            reason="release_gate_blocked",
            gate_decision=decision,
        )

    result = operation()
    return ProtectedOperationResult(
        allowed=True,
        reason="release_gate_allowed",
        gate_decision=decision,
        operation_result=result,
    )
