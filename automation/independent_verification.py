"""Independent verification evidence gates for specialist-produced artifacts.

This module evaluates submitted evidence; it does not execute tests, scan code,
or certify an artifact merely because a reviewer claims success.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Iterable

_REQUIRED_CHECKS = frozenset({
    "acceptance_criteria",
    "tests",
    "security",
    "architecture",
    "claims_and_provenance",
})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    reviewer_agent_id: str
    status: str
    evidence_reference: str
    summary: str

    def validate(self) -> None:
        if self.name not in _REQUIRED_CHECKS:
            raise ValueError("unsupported_verification_check")
        if not isinstance(self.reviewer_agent_id, str) or not _ID.fullmatch(self.reviewer_agent_id):
            raise ValueError("invalid_reviewer_agent_id")
        if self.status not in {"passed", "failed", "blocked", "not_run"}:
            raise ValueError("invalid_verification_status")
        if not isinstance(self.evidence_reference, str) or not self.evidence_reference.strip() or len(self.evidence_reference) > 1000:
            raise ValueError("verification_evidence_reference_required")
        if not isinstance(self.summary, str) or not self.summary.strip() or len(self.summary) > 2000:
            raise ValueError("verification_summary_required")


@dataclass(frozen=True)
class VerificationReport:
    artifact_sha256: str
    author_agent_id: str
    checks: tuple[VerificationCheck, ...]
    created_at: str

    def validate(self) -> None:
        if not isinstance(self.artifact_sha256, str) or not _SHA256.fullmatch(self.artifact_sha256):
            raise ValueError("artifact_sha256_must_be_lowercase_sha256")
        if not isinstance(self.author_agent_id, str) or not _ID.fullmatch(self.author_agent_id):
            raise ValueError("invalid_author_agent_id")
        if not isinstance(self.checks, tuple) or not self.checks:
            raise ValueError("verification_checks_required")
        names = set()
        for check in self.checks:
            if not isinstance(check, VerificationCheck):
                raise TypeError("checks must contain VerificationCheck values")
            check.validate()
            if check.name in names:
                raise ValueError("duplicate_verification_check")
            names.add(check.name)
            if check.reviewer_agent_id == self.author_agent_id:
                raise ValueError("author_cannot_verify_own_artifact")
        try:
            parsed = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValueError("invalid_verification_timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("verification_timestamp_must_be_timezone_aware")


@dataclass(frozen=True)
class VerificationDecision:
    accepted: bool
    artifact_sha256: str
    passed_checks: tuple[str, ...]
    missing_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    blocked_checks: tuple[str, ...]
    not_run_checks: tuple[str, ...]
    reasons: tuple[str, ...]


def evaluate_verification(
    report: VerificationReport,
    *,
    required_checks: Iterable[str] = _REQUIRED_CHECKS,
) -> VerificationDecision:
    """Fail closed unless every required check passed under a different agent."""
    if not isinstance(report, VerificationReport):
        raise TypeError("report must be a VerificationReport")
    report.validate()
    required = frozenset(required_checks)
    if not required or not required.issubset(_REQUIRED_CHECKS):
        raise ValueError("required_checks contains unsupported or no checks")
    by_name = {check.name: check for check in report.checks}
    missing = tuple(sorted(required - by_name.keys()))
    passed = tuple(sorted(name for name in required & by_name.keys() if by_name[name].status == "passed"))
    failed = tuple(sorted(name for name in required & by_name.keys() if by_name[name].status == "failed"))
    blocked = tuple(sorted(name for name in required & by_name.keys() if by_name[name].status == "blocked"))
    not_run = tuple(sorted(name for name in required & by_name.keys() if by_name[name].status == "not_run"))
    reasons = []
    if missing: reasons.append("required_checks_missing")
    if failed: reasons.append("verification_failed")
    if blocked: reasons.append("verification_blocked")
    if not_run: reasons.append("verification_not_run")
    if missing or failed or blocked or not_run:
        reasons.append("artifact_not_accepted")
    return VerificationDecision(
        accepted=not reasons,
        artifact_sha256=report.artifact_sha256,
        passed_checks=passed,
        missing_checks=missing,
        failed_checks=failed,
        blocked_checks=blocked,
        not_run_checks=not_run,
        reasons=tuple(reasons),
    )


def sha256_artifact(content: bytes) -> str:
    """Return a stable digest for the exact bytes reviewed."""
    if not isinstance(content, bytes):
        raise TypeError("artifact content must be bytes")
    return hashlib.sha256(content).hexdigest()


def new_verification_timestamp() -> str:
    """Generate an aware UTC timestamp for report creation."""
    return datetime.now(timezone.utc).isoformat()
