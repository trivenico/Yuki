# WI-6P — Supervised security pipeline workers

This milestone adds a small host-owned runtime for three bounded worker roles: `ingestion`, `alert_sync`, and `alert_dispatch`. It deliberately does not construct provider clients, discover credentials, or own application-specific source adapters.

## Wiring

Construct each operation from the existing components:

- ingestion: call one bounded `DurableSecurityEventIngestor` page for each configured source. For multiple sources, keep each call bounded and make the operation return only after the configured source steps complete.
- alert sync: call `SecurityAlertOutbox.sync_incidents(monitor, operator, limit=...)`.
- alert dispatch: call `SecurityAlertOutbox.deliver_one(worker_id, sender)` once per tick.

Pass those operations to `SecurityPipelineWorker`, then give the workers to `SecurityPipelineSupervisor`. Each operation must do bounded work and return promptly. The dispatcher performs one claim/delivery attempt per tick; the outbox remains responsible for leases, fencing, retries, and dead-letter transitions.

## Runtime behavior

- Successful, empty, and busy ticks refresh the role heartbeat. Failed ticks do not refresh it.
- Exceptions are contained at the worker boundary; only a generic failed status and retry delay are returned. Exception text is not persisted or exposed.
- Retry delay grows exponentially and is capped by `max_retry_seconds`. Normal successful ticks use `interval_seconds`.
- The supervisor starts one non-daemon thread per unique role. `stop()` signals all workers and joins them within one shared shutdown deadline.
- A blocking operation cannot be forcibly interrupted by Python threads. Keep operations bounded and configure network/database timeouts shorter than the supervisor shutdown budget. The host should call `stop()` from its own shutdown hook and treat a false `stopped` result as an unclean shutdown.
- The host process manager remains responsible for restart-on-crash, single-instance policy, signals, and deployment lifecycle.

## Safety and deployment

Use stable, non-secret worker IDs. Do not place credentials or principal data in worker IDs, operation results, logs, or exception messages. Heartbeats are liveness signals only; required cursor freshness from WI-6O remains the proof of source progress. The outbox's own leases and retry state remain authoritative for alert delivery.

The worker threads run in one process and use the existing local SQLite stores. This is a single-host starter, not a distributed scheduler. Multi-host deployments need shared lease/state storage or explicit per-instance ownership, process-level supervision, metrics, and tested shutdown/restart behavior.

Tests were added but not executed in this connector-only session.
