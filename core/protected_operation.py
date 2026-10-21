"""Enforce context-bound release-gate decisions before protected side effects.

Callers must provide the exact repository, PR, and commit the operation will affect.
Every sensitive caller must use this wrapper and remove alternate ungated paths.
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


def _valid_target(repository: object, pull_request: object, head_sha: object) -> bool:
    return (
        isinstance(repository, str)
        and repository == repository.strip()
        and repository.count("/") == 1
        and all(part and part not in {".", ".."} and part == part.strip()
                for part in repository.split("/"))
        and type(pull_request) is int
        and pull_request > 0
        and isinstance(head_sha, str)
        and _SHA_RE.fullmatch(head_sha) is not None
    )


def _valid_allow_decision(decision: ReleaseGateDecision) -> bool:
    """Validate the full invariant, not just a derived boolean property."""
    return (
        decision.decision == "ready-for-required-checks"
        and isinstance(decision.reasons, tuple)
        and len(decision.reasons) == 0
        and isinstance(decision.repository, str)
        and _valid_target(
            decision.repository, decision.pull_request, decision.expected_head_sha
        )
        and type(decision.evidence_id) is int
        and decision.evidence_id > 0
        and decision.audit_persisted is True
    )


def run_protected_operation(
    *,
    evaluate_gate: Callable[[], ReleaseGateDecision],
    operation: Callable[[], T],
    target_repository: str,
    target_pull_request: int,
    target_head_sha: str,
) -> ProtectedOperationResult:
    """Run only when a valid persisted allow decision matches the exact target.

    Invalid input, gate exceptions, malformed/inconsistent decisions, missing audit
    persistence, and context mismatch all block before the operation callback. Errors
    raised by the operation itself propagate to the caller's recovery policy.
    """
    if not callable(evaluate_gate) or not callable(operation):
        return ProtectedOperationResult(
            allowed=False, reason="protected_operation_callback_invalid", gate_decision=None
        )
    if not _valid_target(target_repository, target_pull_request, target_head_sha):
        return ProtectedOperationResult(
            allowed=False, reason="protected_operation_target_invalid", gate_decision=None
        )

    try:
        decision = evaluate_gate()
    except Exception:
        return ProtectedOperationResult(
            allowed=False, reason="release_gate_evaluation_failed", gate_decision=None
        )

    if not isinstance(decision, ReleaseGateDecision):
        return ProtectedOperationResult(
            allowed=False, reason="release_gate_returned_invalid_decision", gate_decision=None
        )
    if not _valid_allow_decision(decision):
        return ProtectedOperationResult(
            allowed=False, reason="release_gate_decision_inconsistent", gate_decision=decision
        )

    if (
        decision.repository.casefold() != target_repository.casefold()
        or decision.pull_request != target_pull_request
        or decision.expected_head_sha.casefold() != target_head_sha.casefold()
    ):
        return ProtectedOperationResult(
            allowed=False, reason="release_gate_target_mismatch", gate_decision=decision
        )

    result = operation()
    return ProtectedOperationResult(
        allowed=True, reason="release_gate_allowed",
        gate_decision=decision, operation_result=result
    )
