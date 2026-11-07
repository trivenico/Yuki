"""Deterministic incident pattern analysis and remediation recommendations.

This module is deliberately advisory: it never mutates incident, task, lease,
worker, or dispatch state. Inputs are bounded, authorized projections supplied
by the host.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping


@dataclass(frozen=True)
class DeploymentEvidence:
    commit_sha: str
    deployed_at: str
    environment: str


@dataclass(frozen=True)
class IncidentEvidence:
    task_id: str
    worker_id: str
    state: str
    attempts: int
    max_attempts: int
    last_reason_code: str
    updated_at: str


@dataclass(frozen=True)
class IncidentFinding:
    code: str
    severity: str
    count: int
    affected_tasks: tuple[str, ...]
    affected_workers: tuple[str, ...]
    summary: str
    recommendation_codes: tuple[str, ...]
    related_commits: tuple[str, ...]


@dataclass(frozen=True)
class RemediationRecommendation:
    code: str
    priority: str
    description: str
    requires_human_approval: bool = True


@dataclass(frozen=True)
class IncidentIntelligenceReport:
    generated_at: str
    incidents_analyzed: int
    findings: tuple[IncidentFinding, ...]
    recommendations: tuple[RemediationRecommendation, ...]
    evidence_limitations: tuple[str, ...]


_REASON_GUIDANCE = {
    "no_eligible_replacement": (
        "replacement_capacity", "high",
        "No eligible replacement reviewer was available.",
        ("review_reviewer_capacity", "verify_reviewer_eligibility"),
    ),
    "replacement_resolver_failed": (
        "replacement_resolution", "high",
        "The trusted replacement resolver raised an exception.",
        ("inspect_replacement_resolver", "verify_resolver_dependencies"),
    ),
    "invalid_replacement_identity": (
        "replacement_resolution", "critical",
        "The replacement resolver returned an invalid reviewer identity.",
        ("inspect_replacement_resolver", "audit_reviewer_identity_policy"),
    ),
    "task_or_lease_changed": (
        "recovery_race", "medium",
        "Task or lease state changed during recovery.",
        ("inspect_concurrent_recovery", "review_lease_ttl"),
    ),
    "lease_reassigned": (
        "recovered", "info",
        "A reviewer lease was successfully reassigned.",
        ("monitor_recovery_rate",),
    ),
}


class ReviewerIncidentIntelligence:
    """Build evidence-linked findings from authorized incident projections."""

    def __init__(
        self, *, correlation_window_seconds: int = 1800,
        repeated_failure_threshold: int = 3,
    ) -> None:
        if type(correlation_window_seconds) is not int or not 1 <= correlation_window_seconds <= 86400:
            raise ValueError("invalid_incident_correlation_window")
        if type(repeated_failure_threshold) is not int or not 2 <= repeated_failure_threshold <= 100:
            raise ValueError("invalid_repeated_failure_threshold")
        self.correlation_window_seconds = correlation_window_seconds
        self.repeated_failure_threshold = repeated_failure_threshold

    @staticmethod
    def _parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("incident_timestamp_must_be_timezone_aware")
        return parsed.astimezone(timezone.utc)

    def analyze(
        self, incidents: Iterable[IncidentEvidence], *,
        deployments: Iterable[DeploymentEvidence] = (),
    ) -> IncidentIntelligenceReport:
        rows = tuple(incidents)
        releases = tuple(deployments)
        if len(rows) > 5000 or len(releases) > 500:
            raise ValueError("incident_intelligence_input_limit_exceeded")
        for row in rows:
            if row.state not in ("open", "cooldown", "recovered", "dead_letter"):
                raise ValueError("invalid_incident_evidence_state")
            if type(row.attempts) is not int or type(row.max_attempts) is not int or not 0 <= row.attempts <= row.max_attempts <= 100:
                raise ValueError("invalid_incident_evidence_attempts")
            if not row.task_id or not row.worker_id or not row.last_reason_code:
                raise ValueError("incomplete_incident_evidence")
            self._parse(row.updated_at)
        for release in releases:
            if not release.commit_sha or not release.environment:
                raise ValueError("incomplete_deployment_evidence")
            self._parse(release.deployed_at)

        by_reason: dict[str, list[IncidentEvidence]] = defaultdict(list)
        by_worker: dict[str, list[IncidentEvidence]] = defaultdict(list)
        for row in rows:
            by_reason[row.last_reason_code].append(row)
            by_worker[row.worker_id].append(row)

        findings: list[IncidentFinding] = []
        recommendations: dict[str, RemediationRecommendation] = {}

        def add_recommendation(code: str, priority: str, description: str) -> None:
            existing = recommendations.get(code)
            rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            candidate = RemediationRecommendation(code, priority, description)
            if existing is None or rank[priority] < rank[existing.priority]:
                recommendations[code] = candidate

        for reason, group in by_reason.items():
            guidance = _REASON_GUIDANCE.get(reason)
            if guidance is None:
                category, severity = "unclassified", "medium"
                summary = "An incident reason has no registered diagnostic guidance."
                rec_codes = ("inspect_incident_evidence",)
            else:
                category, severity, summary, rec_codes = guidance
            if reason != "lease_reassigned" and len(group) >= self.repeated_failure_threshold:
                severity = "critical" if severity == "critical" else "high"
                summary = f"Repeated pattern: {summary}"
            commits: set[str] = set()
            for incident in group:
                occurred = self._parse(incident.updated_at)
                for release in releases:
                    deployed = self._parse(release.deployed_at)
                    delta = (occurred - deployed).total_seconds()
                    if 0 <= delta <= self.correlation_window_seconds:
                        commits.add(release.commit_sha)
            findings.append(IncidentFinding(
                code=category, severity=severity, count=len(group),
                affected_tasks=tuple(sorted({x.task_id for x in group})),
                affected_workers=tuple(sorted({x.worker_id for x in group})),
                summary=summary, recommendation_codes=tuple(rec_codes),
                related_commits=tuple(sorted(commits)),
            ))
            for code in rec_codes:
                descriptions = {
                    "review_reviewer_capacity": "Review reviewer pool capacity and availability.",
                    "verify_reviewer_eligibility": "Verify reviewer eligibility and routing constraints.",
                    "inspect_replacement_resolver": "Inspect the trusted replacement resolver and its dependency health.",
                    "verify_resolver_dependencies": "Validate resolver dependencies, timeouts, and failure handling.",
                    "audit_reviewer_identity_policy": "Audit reviewer identity validation and authorization policy.",
                    "inspect_concurrent_recovery": "Inspect competing recovery controllers and stale-version conflicts.",
                    "review_lease_ttl": "Review worker heartbeat and reviewer lease TTL alignment.",
                    "monitor_recovery_rate": "Monitor successful recovery rate and recurrence.",
                    "inspect_incident_evidence": "Collect additional evidence for the unclassified incident reason.",
                }
                add_recommendation(code, severity if severity != "info" else "low",
                                   descriptions[code])

        dead = [x for x in rows if x.state == "dead_letter"]
        if dead:
            findings.append(IncidentFinding(
                code="dead_letter_backlog", severity="critical",
                count=len(dead),
                affected_tasks=tuple(sorted(x.task_id for x in dead)),
                affected_workers=tuple(sorted({x.worker_id for x in dead})),
                summary="Dead-letter incidents require authorized operator review.",
                recommendation_codes=("review_dead_letter_backlog",),
                related_commits=(),
            ))
            add_recommendation(
                "review_dead_letter_backlog", "critical",
                "Review dead-letter evidence and explicitly approve any remediation.",
            )

        repeated_workers = {
            worker: group for worker, group in by_worker.items()
            if len(group) >= self.repeated_failure_threshold
            and any(x.state != "recovered" for x in group)
        }
        for worker, group in repeated_workers.items():
            findings.append(IncidentFinding(
                code="worker_recurring_incidents", severity="high",
                count=len(group), affected_tasks=tuple(sorted({x.task_id for x in group})),
                affected_workers=(worker,),
                summary="One reviewer identity is associated with repeated incidents.",
                recommendation_codes=("inspect_worker_health",),
                related_commits=(),
            ))
            add_recommendation(
                "inspect_worker_health", "high",
                "Inspect worker heartbeat, process restarts, resource pressure, and host health.",
            )

        findings.sort(key=lambda x: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[x.severity],
            -x.count, x.code,
        ))
        return IncidentIntelligenceReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            incidents_analyzed=len(rows), findings=tuple(findings),
            recommendations=tuple(sorted(
                recommendations.values(),
                key=lambda x: ({"critical": 0, "high": 1, "medium": 2, "low": 3}[x.priority], x.code),
            )),
            evidence_limitations=(
                "Analysis is deterministic and uses only supplied authorized projections.",
                "Deployment correlation is temporal proximity, not proof of causation.",
                "No live host telemetry or source-control history is queried by this analyzer.",
                "Recommendations are advisory; no privileged remediation is executed.",
            ),
        )


__all__ = [
    "DeploymentEvidence", "IncidentEvidence", "IncidentFinding",
    "RemediationRecommendation", "IncidentIntelligenceReport",
    "ReviewerIncidentIntelligence",
]
