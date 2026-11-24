# WI-6Q — Security pipeline recovery integration tests

## Scope

Adds cross-component tests using real, isolated SQLite databases for the monitor, ingestion cursors, alert outbox, and worker heartbeats.

## Recovery scenarios

- Replays an authorization-audit page after the monitor commits but before the ingestion cursor commits. The deterministic signal ID must prevent duplicate monitor events.
- Verifies a failed worker tick does not refresh its heartbeat, while a subsequent successful tick resets the failure count and restores liveness.
- Verifies operational health transitions from unhealthy to healthy after required cursor initialization and all worker heartbeats recover, then becomes unhealthy when the cursor ages beyond its threshold.
- Simulates dispatch-worker crashes by allowing outbox leases to expire twice; the configured attempt limit dead-letters the alert and health reports the dead-letter condition.

## Boundaries

The tests use temporary local SQLite databases and a synthetic audit source. They do not claim multi-host correctness, provider delivery, process-kill behavior, or deployment validation. Tests are added but must be run by repository CI or a maintainer's test environment.
