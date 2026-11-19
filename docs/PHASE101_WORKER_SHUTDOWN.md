# Phase 101 — workforce stop-admission boundary

## Worker stop request

`WorkforceWorker.request_stop()` provides a thread-safe stop-admission signal for an explicitly managed worker process. The stop request is serialized with the task-claim operation. Once the call returns, subsequent `run_once()` invocations return a stopping result and do not claim queued work.

A task already claimed before the stop request is not cancelled, failed, or released. Its executor call continues and the existing heartbeat thread continues renewing the lease until execution/review returns. This avoids treating a potentially side-effecting in-flight task as safe to replay.

## Lifecycle integration boundary

The workforce composition currently does not start a background worker thread or process; callers invoke `WorkforceApplicationRuntime.run_once()` from an explicitly managed worker process. The stop request is therefore a primitive for that supervisor to call before it stops scheduling new invocations.

This change is not a complete graceful-shutdown coordinator. The process supervisor must:
1. Stop scheduling new `run_once()` calls.
2. Call `workforce_application.worker.request_stop()`.
3. Allow any active `run_once()` call to finish while the engine remains open so lease heartbeats can continue.
4. Close the workforce runtime only after the active call has returned, or terminate the process and rely on lease expiry plus startup recovery.

Do not close the task engine concurrently with active execution. Do not cancel a running task or reclaim its lease early: the external side effect may already have occurred. If the process is forcibly terminated, startup recovery can reclaim the task only after its lease expires; single-attempt side-effect tasks remain failed rather than automatically replayed.

## Verification boundary

`tests/test_workforce_worker.py` adds a regression test proving that a stop request leaves a queued task queued and does not invoke the executor. The test has been committed but has not been independently executed here. Check the latest GitHub Actions results before treating it as passing.
