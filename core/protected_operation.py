"""Enforce context-bound release-gate decisions before protected side effects.

Callers must provide the exact repository, PR, and commit the operation will affect.
This module cannot discover all protected call sites: applications must route each
sensitive operation through this wrapper and remove alternate ungated paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, TypeVar

from core.release_gate import ReleaseGateDecision

T = TypeVar("T")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


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
    target_repository: str,
    target_pull_request: int,
    target_head_sha: str,
) -> ProtectedOperationResult:
    """Run only if a persisted allow decision matches the exact target context.

    Fail closed on evaluation errors, malformed decisions, missing audit persistence,
    or repository/PR/SHA mismatch. Exceptions from the protected operation itself are
    propagated for the caller's recovery policy.
    """
    if (
        not isinstance(target_repository, str)
        or target_repository.count("/") != 1
        or any(part in {"", ".", ".."} for part in target_repository.split("/"))
        or type(target_pull_request) is not int
        or target_pull_request < 1
        or not isinstance(target_head_sha, str)
        or not _SHA_RE.fullmatch(target_head_sha)
    ):
        return ProtectedOperationResult(
            allowed=False,
            reason="protected_operation_target_invalid",
            gate_decision=None,
        )

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

    if (
        decision.repository.casefold() != target_repository.casefold()
        or decision.pull_request != target_pull_request
        or decision.expected_head_sha.casefold() != target_head_sha.casefold()
    ):
        return ProtectedOperationResult(
            allowed=False,
            reason="release_gate_target_mismatch",
            gate_decision=decision,
        )

    result = operation()
    return ProtectedOperationResult(
        allowed=True,
        reason="release_gate_allowed",
        gate_decision=decision,
        operation_result=result,
    )
