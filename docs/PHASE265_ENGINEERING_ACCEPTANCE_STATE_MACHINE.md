# Phase 265 — Durable engineering acceptance state machine

## Delivered

Added `automation/engineering_acceptance.py` with a SQLite-backed acceptance
lifecycle separate from worker execution status.

States:
- `awaiting_artifact`
- `awaiting_verification`
- `accepted`
- `rejected`
- `remediation`

The API supports:
- Authorized registration of a coordinator task identity.
- Artifact attachment only when plan, subtask, and task IDs match.
- Verification refresh against the linked artifact's durable report history.
- Acceptance only when an accepted independent report exists.
- Rejection when reports exist but none are accepted.
- Explicit remediation requests and attachment of a new artifact revision.
- An append-only transition event history.
- A dependency-release predicate that returns true only for `accepted`.

## Safety boundaries

This store does not yet mutate `SpecialistAgentCoordinator` task states or
prevent `advance()` from dispatching dependents. The dependency-release
predicate is available to the coordinator integration, but is not wired into
it in this phase. Worker completion remains distinct from engineering acceptance.

The reviewer and acceptance authorization callbacks must be connected to
authenticated actor identities and registered roles by the application.
Evidence report provenance still depends on trusted evidence producers.

## Tests

Added `tests/test_engineering_acceptance.py` for waiting states, accepted and
rejected outcomes, remediation with a new artifact digest, task identity
mismatch, and authorization failure.

Tests have been authored but not executed here. No CI or production-readiness
claim is made.

## Next integration

Wire this acceptance store into coordinator dependency readiness and release
semantics. A task's worker execution may complete, but dependent engineering
tasks must remain blocked until the predecessor is accepted under the configured
policy. Preserve compatibility for plans that explicitly opt out of artifact
verification only in non-production/test contexts.
