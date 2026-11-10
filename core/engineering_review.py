"""Structured engineering review evidence and conservative merge-gate evaluation.

Model-authored recommendations are advisory. This module validates evidence shape and
computes a conservative gate result; it does not replace GitHub branch protection,
CI status checks, human approval, or repository authorization.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Iterable


SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
RECOMMENDATIONS = frozenset({"block", "needs-human-review", "ready-for-required-checks"})
CHECK_STATUSES = frozenset({"passed", "failed", "skipped", "not_run"})


class ReviewReportError(ValueError):
    """Review evidence is malformed or exceeds safe limits."""


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    file: str
    scenario: str
    impact: str
    suggested_fix: str
    line: int | None = None

    def validate(self) -> None:
        if self.severity not in SEVERITIES:
            raise ReviewReportError("finding severity is invalid")
        for key in ("file", "scenario", "impact", "suggested_fix"):
            value = getattr(self, key)
            if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                raise ReviewReportError(f"finding {key} must be a non-empty string up to 4000 chars")
        if self.line is not None and (type(self.line) is not int or self.line < 1):
            raise ReviewReportError("finding line must be a positive integer")


@dataclass(frozen=True)
class CheckEvidence:
    name: str
    status: str
    command: str | None = None
    summary: str = ""

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ReviewReportError("check name is invalid")
        if self.status not in CHECK_STATUSES:
            raise ReviewReportError("check status is invalid")
        if self.command is not None and (not isinstance(self.command, str) or len(self.command) > 2000):
            raise ReviewReportError("check command is invalid")
        if not isinstance(self.summary, str) or len(self.summary) > 4000:
            raise ReviewReportError("check summary is invalid")


@dataclass(frozen=True)
class EngineeringReviewReport:
    task_id: str
    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)
    checks: tuple[CheckEvidence, ...] = field(default_factory=tuple)
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    compatibility_risks: tuple[str, ...] = field(default_factory=tuple)
    skipped_checks: tuple[str, ...] = field(default_factory=tuple)
    residual_risks: tuple[str, ...] = field(default_factory=tuple)
    recommendation: str = "needs-human-review"
    schema_version: int = 1

    def validate(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip() or len(self.task_id) > 200:
            raise ReviewReportError("task_id is required")
        if self.schema_version != 1:
            raise ReviewReportError("unsupported review schema version")
        if self.recommendation not in RECOMMENDATIONS:
            raise ReviewReportError("recommendation is invalid")
        if len(self.findings) > 500 or len(self.checks) > 200 or len(self.changed_files) > 2000:
            raise ReviewReportError("report exceeds item-count limits")
        for finding in self.findings:
            finding.validate()
        for check in self.checks:
            check.validate()
        for collection_name in ("changed_files", "compatibility_risks", "skipped_checks", "residual_risks"):
            collection = getattr(self, collection_name)
            if not isinstance(collection, tuple) or len(collection) > 2000:
                raise ReviewReportError(f"{collection_name} must be a bounded tuple")
            for item in collection:
                if not isinstance(item, str) or not item.strip() or len(item) > 4000:
                    raise ReviewReportError(f"{collection_name} contains an invalid item")
        if len(self.to_json()) > 1_000_000:
            raise ReviewReportError("report exceeds 1 MB")

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, allow_nan=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "EngineeringReviewReport":
        if not isinstance(raw, str) or len(raw) > 1_000_000:
            raise ReviewReportError("serialized report is invalid or too large")
        try:
            data: dict[str, Any] = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ReviewReportError("serialized report is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ReviewReportError("serialized report must be a JSON object")
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        if set(data) - allowed:
            raise ReviewReportError("serialized report contains unknown fields")
        try:
            data["findings"] = tuple(ReviewFinding(**item) for item in data.get("findings", ()))
            data["checks"] = tuple(CheckEvidence(**item) for item in data.get("checks", ()))
            for key in ("changed_files", "compatibility_risks", "skipped_checks", "residual_risks"):
                if key in data:
                    data[key] = tuple(data[key])
            report = cls(**data)
        except (TypeError, KeyError) as exc:
            raise ReviewReportError("serialized report has an invalid structure") from exc
        report.validate()
        return report


def evaluate_review_gate(
    report: EngineeringReviewReport,
    *,
    required_checks: Iterable[str],
    require_human_approval: bool = False,
    human_approved: bool = False,
) -> str:
    """Return a conservative advisory gate status.

    Any critical/high finding, failed check, missing/not-run/skipped required check, or
    skipped check blocks progression. Human approval can be required by caller policy.
    This result must never be treated as authorization to execute privileged actions.
    """
    report.validate()
    required = set(required_checks)
    if any(not isinstance(name, str) or not name.strip() for name in required):
        raise ReviewReportError("required check names must be non-empty strings")
    if any(f.severity in {"critical", "high"} for f in report.findings):
        return "block"
    by_name = {check.name: check.status for check in report.checks}
    if any(by_name.get(name) != "passed" for name in required):
        return "block"
    if report.skipped_checks or any(c.status in {"failed", "skipped", "not_run"} for c in report.checks):
        return "block"
    if require_human_approval and not human_approved:
        return "needs-human-review"
    if report.recommendation != "ready-for-required-checks":
        return "needs-human-review"
    return "ready-for-required-checks"
