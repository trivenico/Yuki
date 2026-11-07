# Phase 318 — Autonomous incident intelligence

## Scope

Adds deterministic, bounded incident pattern analysis and an authorized adapter
that reads a snapshot from the Phase 317 incident journal. The analyzer is
advisory and does not mutate tasks, leases, workers, dispatches, or incident
records.

## Analysis capabilities

- Groups incidents by normalized reason category.
- Flags repeated reason patterns using a configurable threshold.
- Identifies reviewer identities associated with repeated unresolved incidents.
- Elevates dead-letter backlog to critical severity.
- Produces prioritized remediation recommendation codes and descriptions.
- Optionally correlates incident timestamps with supplied deployment timestamps
  within a bounded correlation window.

Deployment correlation means temporal proximity only. It is not evidence that
a deployment caused an incident. The analyzer does not query GitHub, host
telemetry, logs, or deployment systems itself; those sources must be gathered
by a trusted host and passed as evidence.

## Authorization and boundaries

`analyze_recovery_incidents` obtains a bounded snapshot through
`ReviewerRecoveryIncidentStore.list_incidents`, which enforces the configured
`review:recovery:list` authorization scope. The analyzer accepts projections,
not database handles or credentials.

Recommendations always require human approval by default. The module never
executes dead-letter remediation, changes reviewer identity, rotates leases,
requeues dispatches, or invokes deployment controls.

## Evidence quality

- Timestamps must be timezone-aware.
- Incident and deployment inputs are bounded.
- Findings include affected task and worker identifiers and correlated commit
  SHAs where temporal evidence exists.
- Unknown reason codes are surfaced as unclassified rather than silently
  discarded.
- Reports explicitly disclose that only supplied evidence was analyzed and
  that temporal correlation is not causation.

## Verification

```bash
python -m unittest tests.test_reviewer_incident_intelligence tests.test_reviewer_incident_analysis tests.test_reviewer_recovery_incidents tests.test_reviewer_worker_recovery -v
```

Tests cover repeated patterns, deployment time correlation, dead-letter
escalation, human approval, malformed timestamps, input bounds, and adapter
authorization. Tests are authored; no execution claim is made until a CI or
local run confirms.
