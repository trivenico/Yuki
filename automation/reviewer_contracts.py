"""Bounded contracts for independent artifact-review tasks and findings.

Contracts validate reviewer output shape and bind it to an immutable artifact
digest. They do not authenticate the reviewer or prove that evidence is true;
submission remains subject to VerificationEvidenceStore authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from automation.reviewer_team import REVIEWER_CAPABILITIES

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})
_FINDING_STATES = frozenset({"open", "resolved", "accepted_risk"})
_RESULT_STATUSES = frozenset({"passed", "failed", "blocked", "not_run"})
_MAX_REQUIREMENTS = 50
_MAX_FINDINGS = 100


class ReviewContractError(ValueError):
    """A review task or reviewer result violates its bounded contract."""


@dataclass(frozen=True)
class ReviewTask:
    task_id: str
    artifact_sha256: str
    author_agent_id: str
    reviewer_agent_id: str
    check_name: str
    requirements: tuple[str, ...]
    evidence_references: tuple[str, ...]

    def validate(self) -> None:
        if not isinstance(self.task_id, str) or not _ID.fullmatch(self.task_id):
            raise ReviewContractError("invalid_review_task_id")
        if not isinstance(self.artifact_sha256, str) or not _SHA256.fullmatch(self.artifact_sha256):
            raise ReviewContractError("invalid_artifact_sha256")
        for name, value in (("author", self.author_agent_id), ("reviewer", self.reviewer_agent_id)):
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ReviewContractError("invalid_" + name + "_agent_id")
        if self.author_agent_id == self.reviewer_agent_id:
            raise ReviewContractError("reviewer_must_differ_from_author")
        if not isinstance(self.check_name, str) or self.check_name not in REVIEWER_CAPABILITIES:
            raise ReviewContractError("unsupported_review_check")
        if not isinstance(self.requirements, tuple) or not 1 <= len(self.requirements) <= _MAX_REQUIREMENTS:
            raise ReviewContractError("review_requirements_invalid")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in self.requirements):
            raise ReviewContractError("review_requirement_invalid")
        if len(set(self.requirements)) != len(self.requirements):
            raise ReviewContractError("duplicate_review_requirement")
        if not isinstance(self.evidence_references, tuple) or len(self.evidence_references) > 100:
            raise ReviewContractError("review_evidence_references_invalid")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in self.evidence_references):
            raise ReviewContractError("review_evidence_reference_invalid")


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    severity: str
    state: str
    title: str
    detail: str
    evidence_reference: str
    recommendation: str = ""

    def validate(self) -> None:
        if not isinstance(self.finding_id, str) or not _ID.fullmatch(self.finding_id):
            raise ReviewContractError("invalid_finding_id")
        if not isinstance(self.severity, str) or self.severity not in _SEVERITIES:
            raise ReviewContractError("invalid_finding_severity")
        if not isinstance(self.state, str) or self.state not in _FINDING_STATES:
            raise ReviewContractError("invalid_finding_state")
        for field, value, limit in (
            ("title", self.title, 200), ("detail", self.detail, 4000),
            ("evidence_reference", self.evidence_reference, 1000),
            ("recommendation", self.recommendation, 2000),
        ):
            if not isinstance(value, str) or (field != "recommendation" and not value.strip()) or len(value) > limit:
                raise ReviewContractError("invalid_finding_" + field)


@dataclass(frozen=True)
class ReviewResult:
    task_id: str
    artifact_sha256: str
    reviewer_agent_id: str
    check_name: str
    status: str
    summary: str
    findings: tuple[ReviewFinding, ...]

    def validate(self, task: ReviewTask) -> None:
        if not isinstance(task, ReviewTask):
            raise TypeError("review_task_required")
        task.validate()
        if (
            self.task_id != task.task_id
            or self.artifact_sha256 != task.artifact_sha256
            or self.reviewer_agent_id != task.reviewer_agent_id
            or self.check_name != task.check_name
        ):
            raise ReviewContractError("review_result_task_binding_mismatch")
        if not isinstance(self.status, str) or self.status not in _RESULT_STATUSES:
            raise ReviewContractError("invalid_review_result_status")
        if not isinstance(self.summary, str) or not self.summary.strip() or len(self.summary) > 2000:
            raise ReviewContractError("invalid_review_summary")
        if not isinstance(self.findings, tuple) or len(self.findings) > _MAX_FINDINGS:
            raise ReviewContractError("review_findings_invalid")
        seen = set()
        for finding in self.findings:
            if not isinstance(finding, ReviewFinding):
                raise ReviewContractError("review_finding_type_invalid")
            finding.validate()
            if finding.finding_id in seen:
                raise ReviewContractError("duplicate_finding_id")
            seen.add(finding.finding_id)
        blockers = tuple(
            finding for finding in self.findings
            if finding.state == "open" and finding.severity in {"high", "critical"}
        )
        if self.status == "passed" and blockers:
            raise ReviewContractError("passed_review_contains_open_blockers")
        if self.status == "passed" and any(
            finding.state == "open" and finding.severity == "medium"
            for finding in self.findings
        ):
            raise ReviewContractError("passed_review_contains_open_medium_findings")


def build_review_result(
    task: ReviewTask,
    *,
    status: str,
    summary: str,
    findings: Iterable[ReviewFinding] = (),
) -> ReviewResult:
    """Create and validate a result bound to the supplied reviewer task."""
    if not isinstance(task, ReviewTask):
        raise TypeError("review_task_required")
    result = ReviewResult(
        task_id=task.task_id,
        artifact_sha256=task.artifact_sha256,
        reviewer_agent_id=task.reviewer_agent_id,
        check_name=task.check_name,
        status=status,
        summary=summary,
        findings=tuple(findings),
    )
    result.validate(task)
    return result


__all__ = [
    "ReviewContractError", "ReviewTask", "ReviewFinding", "ReviewResult",
    "build_review_result",
]
