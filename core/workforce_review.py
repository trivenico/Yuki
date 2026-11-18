"""Structured, fail-closed independent quality review adapter.

The evaluator is injected so deployments can use a separate model, deterministic
test runner, or human review service. This module does not call an LLM or execute
submitted artifacts itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


REQUIRED_CHECKS = frozenset({
    "task_scope",
    "correctness",
    "security",
    "verification",
})


@dataclass(frozen=True)
class QualityReviewReport:
    approved: bool
    status: str
    findings: tuple[str, ...]
    checks: Mapping[str, bool]
    reviewer_id: str


class IndependentQualityReviewer:
    """Validate a structured evaluation from a separately identified reviewer."""

    def __init__(
        self,
        *,
        reviewer_id: str,
        evaluator: Callable[..., Mapping[str, Any]],
        required_checks: frozenset[str] = REQUIRED_CHECKS,
    ):
        if not isinstance(reviewer_id, str) or not reviewer_id.strip() or len(reviewer_id) > 256:
            raise ValueError("reviewer_id must be non-empty bounded text")
        if not callable(evaluator):
            raise TypeError("evaluator must be callable")
        if not isinstance(required_checks, frozenset) or not required_checks:
            raise ValueError("required_checks must be a non-empty frozenset")
        if any(not isinstance(item, str) or not item.strip() for item in required_checks):
            raise ValueError("required_checks must contain non-empty strings")
        self.reviewer_id = reviewer_id
        self.evaluator = evaluator
        self.required_checks = required_checks

    def review(
        self,
        *,
        description: str,
        employee_id: str,
        execution_status: str,
        execution_message: str,
    ) -> QualityReviewReport:
        if employee_id == self.reviewer_id:
            return QualityReviewReport(
                False, "self_review_denied", ("Reviewer must differ from task owner.",),
                {}, self.reviewer_id,
            )
        raw = self.evaluator(
            description=description,
            employee_id=employee_id,
            execution_status=execution_status,
            execution_message=execution_message,
            required_checks=tuple(sorted(self.required_checks)),
        )
        if not isinstance(raw, Mapping):
            return QualityReviewReport(False, "invalid_review_report", ("Evaluator did not return an object.",), {}, self.reviewer_id)
        checks = raw.get("checks")
        findings = raw.get("findings")
        if (
            not isinstance(checks, Mapping)
            or set(checks) != set(self.required_checks)
            or any(type(value) is not bool for value in checks.values())
            or not isinstance(findings, (tuple, list))
            or any(not isinstance(item, str) or not item.strip() for item in findings)
        ):
            return QualityReviewReport(False, "invalid_review_report", ("Review report failed schema validation.",), {}, self.reviewer_id)
        approved = all(checks.values()) and len(findings) == 0
        return QualityReviewReport(
            approved=approved,
            status="approved" if approved else "quality_gate_failed",
            findings=tuple(findings[:100]),
            checks=dict(checks),
            reviewer_id=self.reviewer_id,
        )
