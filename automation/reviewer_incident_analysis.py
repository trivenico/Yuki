"""Host-side adapter from authorized recovery journal to incident intelligence."""
from __future__ import annotations

from typing import Iterable

from automation.reviewer_incident_intelligence import (
    DeploymentEvidence, IncidentEvidence, IncidentIntelligenceReport,
    ReviewerIncidentIntelligence,
)
from automation.reviewer_recovery_incidents import ReviewerRecoveryIncidentStore


def analyze_recovery_incidents(
    store: ReviewerRecoveryIncidentStore, *,
    actor_id: str,
    analyzer: ReviewerIncidentIntelligence | None = None,
    deployments: Iterable[DeploymentEvidence] = (),
    limit: int = 500,
) -> IncidentIntelligenceReport:
    """Analyze an authorized bounded incident snapshot without mutating it."""
    if not callable(getattr(store, "list_incidents", None)):
        raise TypeError("reviewer_recovery_incident_store_required")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid_incident_analysis_limit")
    incidents = store.list_incidents(actor_id=actor_id, limit=limit)
    evidence = tuple(IncidentEvidence(
        task_id=item.task_id,
        worker_id=item.worker_id,
        state=item.state,
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        last_reason_code=item.last_reason_code,
        updated_at=item.updated_at,
    ) for item in incidents)
    engine = analyzer or ReviewerIncidentIntelligence()
    if not isinstance(engine, ReviewerIncidentIntelligence):
        raise TypeError("reviewer_incident_intelligence_required")
    return engine.analyze(evidence, deployments=deployments)


__all__ = ["analyze_recovery_incidents"]
