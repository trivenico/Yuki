"""Independent review and verification contracts for supervised evolution.

These contracts validate evidence and produce a gate decision. They do not
invoke an LLM, execute code, approve a plan, or merge a pull request.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable


_SHA = re.compile(r"^[a-f0-9]{40,64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_ALLOWED_CHECKS = frozenset({
    "unit_tests",
    "integration_tests",
    "static_analysis",
    "dependency_audit",
    "secret_scan",
    "security_tests",
})


class EvolutionReviewError(ValueError):
    """Raised when review or verification evidence is malformed."""


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class ReviewEvidence:
    reviewer_id: str
    author_id: str
    commit_sha: str
    verdict: ReviewVerdict
    findings: tuple[str, ...] = ()
    rationale_code: str = "review_complete"


@dataclass(frozen=True)
class CheckResult:
    name: str
    commit_sha: str
    passed: bool
    evidence_id: str


@dataclass(frozen=True)
class VerificationReport:
    commit_sha: str
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        required = _ALLOWED_CHECKS
        by_name = {item.name: item for item in self.checks}
        return (
            set(by_name) == required
            and all(item.passed and item.commit_sha == self.commit_sha for item in self.checks)
        )


@dataclass(frozen=True)
class EvolutionGateDecision:
    allowed_to_propose_merge: bool
    review_verdict: ReviewVerdict
    verification_passed: bool
    reasons: tuple[str, ...]


class EvolutionReviewVerifier:
    """Validate independent review and commit-bound CI evidence."""

    def validate_review(self, evidence: ReviewEvidence, *, expected_commit: str) -> ReviewEvidence:
        if not isinstance(evidence, ReviewEvidence):
            raise EvolutionReviewError("review evidence is required.")
        if not isinstance(expected_commit, str) or not _SHA.fullmatch(expected_commit):
            raise EvolutionReviewError("expected commit SHA is invalid.")
        if not isinstance(evidence.reviewer_id, str) or not _ID.fullmatch(evidence.reviewer_id):
            raise EvolutionReviewError("reviewer identity is invalid.")
        if not isinstance(evidence.author_id, str) or not _ID.fullmatch(evidence.author_id):
            raise EvolutionReviewError("author identity is invalid.")
        if evidence.reviewer_id.casefold() == evidence.author_id.casefold():
            raise EvolutionReviewError("independent reviewer must differ from author.")
        if evidence.commit_sha != expected_commit:
            raise EvolutionReviewError("review evidence is not bound to the expected commit.")
        if not isinstance(evidence.verdict, ReviewVerdict):
            raise EvolutionReviewError("review verdict is invalid.")
        if not isinstance(evidence.findings, tuple) or len(evidence.findings) > 100:
            raise EvolutionReviewError("review findings must be a bounded tuple.")
        for finding in evidence.findings:
            if not isinstance(finding, str) or not finding.strip() or len(finding) > 500:
                raise EvolutionReviewError("review finding is invalid.")
            if any(ord(char) < 32 and char not in "\t" for char in finding):
                raise EvolutionReviewError("review finding contains control characters.")
        if not isinstance(evidence.rationale_code, str) or not _ID.fullmatch(evidence.rationale_code):
            raise EvolutionReviewError("review rationale code is invalid.")
        if evidence.verdict == ReviewVerdict.APPROVE and evidence.findings:
            raise EvolutionReviewError("an approving review cannot contain unresolved findings.")
        if evidence.verdict == ReviewVerdict.REQUEST_CHANGES and not evidence.findings:
            raise EvolutionReviewError("request-changes review must include findings.")
        return evidence

    def validate_verification(
        self, report: VerificationReport, *, expected_commit: str,
    ) -> VerificationReport:
        if not isinstance(report, VerificationReport):
            raise EvolutionReviewError("verification report is required.")
        if not isinstance(expected_commit, str) or not _SHA.fullmatch(expected_commit):
            raise EvolutionReviewError("expected commit SHA is invalid.")
        if report.commit_sha != expected_commit:
            raise EvolutionReviewError("verification report is stale for this commit.")
        if not isinstance(report.checks, tuple) or len(report.checks) != len(_ALLOWED_CHECKS):
            raise EvolutionReviewError("verification report must contain every required check exactly once.")
        names = []
        for item in report.checks:
            if not isinstance(item, CheckResult):
                raise EvolutionReviewError("verification check entry is invalid.")
            if item.name not in _ALLOWED_CHECKS:
                raise EvolutionReviewError("verification contains an unknown check.")
            names.append(item.name)
            if not isinstance(item.passed, bool):
                raise EvolutionReviewError("check result must be boolean.")
            if item.commit_sha != expected_commit:
                raise EvolutionReviewError("a check result is stale for this commit.")
            if not isinstance(item.evidence_id, str) or not _ID.fullmatch(item.evidence_id):
                raise EvolutionReviewError("check evidence identifier is invalid.")
        if len(set(names)) != len(names) or set(names) != _ALLOWED_CHECKS:
            raise EvolutionReviewError("verification report has missing or duplicate checks.")
        return report

    def decide(
        self, review: ReviewEvidence, verification: VerificationReport, *, expected_commit: str,
    ) -> EvolutionGateDecision:
        self.validate_review(review, expected_commit=expected_commit)
        self.validate_verification(verification, expected_commit=expected_commit)
        reasons = []
        if review.verdict != ReviewVerdict.APPROVE:
            reasons.append("independent_review_not_approved")
        if not verification.passed:
            reasons.append("required_verification_not_passed")
        return EvolutionGateDecision(
            allowed_to_propose_merge=not reasons,
            review_verdict=review.verdict,
            verification_passed=verification.passed,
            reasons=tuple(reasons),
        )
