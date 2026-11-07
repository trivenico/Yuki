# Phase 312 — Persistent reviewer worker supervision

## Delivered runtime boundary

`ReviewerWorkerService` wraps the Phase 311 worker with:
- bounded parallel polling (1–32 slots per service instance);
- process-local graceful stop signaling;
- host-provided heartbeat callback with state, active slots, completed count,
  failed cycles, and a sanitized error code;
- wait-for-in-flight-work shutdown semantics;
- idle polling delay to avoid a hot loop.

## Concurrency and deployment

The service runs multiple calls to `ReviewerWorker.run_once(limit=1)`. SQLite's
transactional dispatch claims prevent two workers from claiming the same
outbox record. The configured concurrency limit is local to this service
instance and reviewer identity; it is not a global distributed semaphore.
Run one service instance per reviewer identity unless a shared concurrency
authority is deployed.

## Shutdown and timeouts

Calling `stop()` prevents another polling cycle and waits for active calls to
finish. It does not terminate a thread or cancel a running model/container
execution. Hard execution deadlines require cancellation support in the
underlying executor and lease-fencing semantics; killing only the supervising
thread would be unsafe. Interrupted in-progress work remains under existing
lease expiry and recovery procedures.

## Heartbeats

The host supplies `heartbeat_callback` and decides where to persist or export
health data. Heartbeats contain no credentials, claim tokens, task payloads,
or reviewer output. Callback failures are surfaced rather than silently
treated as healthy operation.

## Verification

Run:

```bash
python -m unittest tests.test_reviewer_worker_service -v
```

The tests validate the local service loop with a concurrent fake worker. They
do not prove distributed concurrency limits, process crash recovery, provider
execution cancellation, or production monitoring delivery.
