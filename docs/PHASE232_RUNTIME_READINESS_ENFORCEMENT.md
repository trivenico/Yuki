# Phase 232 — runtime readiness enforcement

## Delivered

- Added provider/model/capability requirements to the immutable specialist manifest.
- Persisted requirements as bounded structured JSON in the existing SQLite registry.
- Added a backward-compatible migration for existing registry databases.
- Included runtime requirements in the agent identity digest so a plan cannot dispatch after its declared runtime requirements change.
- Added a trusted readiness resolver injection point to the coordinator.
- Enforced readiness during deterministic routing and rechecked it immediately before task creation.
- Missing, raising, malformed, or non-ready evidence fails closed.
- Dispatch-time readiness loss marks the subtask blocked and appends a metadata-only plan event; no task is enqueued.
- Sanitized registry listings expose declared provider/model requirements without exposing credentials.

## Provider boundary

The coordinator does not construct a provider or invent conformance evidence. Production wiring must supply a resolver backed by the active `ProviderRuntimeManager.readiness()` and a matching, genuinely obtained live conformance report. Configuration and reachability alone do not qualify.

## Remaining limitation

The current `ProviderReadiness` record does not carry a cryptographically bound verification timestamp or expiry. This phase rechecks readiness immediately before dispatch but does not yet enforce an evidence age/expiry window. That must be added before treating readiness as time-bounded. The resolver is a trusted dependency and must not be exposed to untrusted callers.

## Tests

Added regression coverage for persisted requirements, missing-resolver fail-closed behavior, and readiness loss between planning and dispatch. These tests use immutable readiness result values; they do not instantiate mock providers or claim live provider conformance.

Local test execution is not claimed. CI results must be checked against the exact commit SHA.
