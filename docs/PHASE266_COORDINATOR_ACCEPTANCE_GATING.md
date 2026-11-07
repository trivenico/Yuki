# Phase 266 — Coordinator acceptance-gated dependencies

## Delivered

Integrated the durable `EngineeringAcceptanceStore` into
`SpecialistAgentCoordinator` as an optional dependency.

When configured:
- A completed predecessor task is not sufficient to release dependent tasks.
- The coordinator registers an acceptance record for a succeeded predecessor.
- A dependent subtask remains pending until the acceptance record reaches
  `accepted`.
- Missing acceptance records are initialized fail-closed.
- Acceptance registration failures raise an `AgentPlanError` instead of
  silently releasing dependent work.
- Existing plans retain their previous behavior when no acceptance store is
  configured, preserving explicit backwards compatibility for non-gated setups.

Added a coordinator integration test which:
1. Dispatches a predecessor and leaves its dependent undispatched.
2. Completes the predecessor worker task.
3. Confirms the dependent remains undispatched while acceptance awaits an artifact.
4. Records exact artifact bytes and a matching independent verification report.
5. Accepts the artifact and confirms the dependent can then be dispatched.

## Important limitations

This integration gates dependency release only. It does not yet automatically
extract artifact bytes from worker outputs, attach reports, or create reviewer
tasks. The test uses the trusted verification workflow APIs directly to model
those steps.

The acceptance store is optional. Production startup must explicitly construct
and inject it to obtain this protection. Tasks without dependents are not
automatically prevented from being reported as worker-succeeded; execution and
acceptance remain distinct concepts.

The acceptance store and coordinator use separate SQLite APIs/connections.
There is no cross-store atomic transaction. Failures are handled conservatively,
but durable reconciliation for every crash window remains future work.

Tests have been authored but not executed in this environment. No CI result is
claimed.
