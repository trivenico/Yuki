# Phase 316 — Supervised recovery operations

## Scope

Adds a polling service around Phase 315's lease-aware recovery supervisor.
It does not duplicate ledger logic or grant the service authority to mutate
task/lease state directly.

## Operational behavior

- Runs one bounded recovery batch at a time; overlapping recovery cycles are
  not launched by this service.
- Emits aggregate running, degraded, and stopped heartbeat records through a
  host-provided callback. No credentials, lease tokens, or task payloads are
  included.
- Isolates cycle exceptions, reports a type-only error code, and retries using
  capped exponential backoff.
- Supports graceful stop after the current bounded cycle and optional
  max-cycle execution for controlled jobs and tests.
- Exposes cumulative recovered/deferred/failed counts and the last report.

## Safety boundaries

The Phase 315 supervisor remains responsible for worker-expiry detection,
replacement resolution, and task eligibility. The task ledger remains the only
authority allowed to validate expired task leases, rotate generations, append
audit events, and atomically requeue dispatches. This service does not retry
individual task transitions and does not replay tasks itself.

A deferred task with no eligible replacement remains deferred; operators should
monitor deferred counts and provide a valid replacement policy. The service
does not automatically loosen lease policy or repeatedly reassign tasks.

## Deployment

Run one recovery service per configured registry/ledger authority unless an
external singleton/election mechanism is configured. Multiple service
instances remain safe at the task-transition boundary because the ledger
checks expected task version and lease expiry transactionally, but duplicate
polling can increase database load. Use bounded batch sizes and conservative
poll intervals.

The callback must be a trusted host-side telemetry sink. Do not attach worker
credentials or expose raw exception messages to monitoring.

## Verification

```bash
python -m unittest tests.test_reviewer_worker_recovery_service tests.test_reviewer_worker_recovery tests.test_reviewer_ledger_recovery -v
```

Tests cover bounded polling, cumulative metrics, heartbeat state, exception
isolation, lifecycle re-entry rejection, and invalid bounds. They are authored
by this phase; no execution claim is made until a CI or local test run confirms.
