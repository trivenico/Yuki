# Phase 102 — bounded workforce shutdown coordination

## Runtime lifecycle

`WorkforceApplicationRuntime.run_once()` now tracks active calls under a condition lock. New calls are rejected with a stopping result once shutdown begins. `shutdown(timeout_seconds)` signals the worker's Phase 101 stop gate and waits for active calls to return, with a validated deadline between zero and one hour.

If the deadline expires, shutdown returns `False` and deliberately leaves the SQLite task engine open. An active worker may still be executing a side effect and renewing its lease; closing the database underneath it would break heartbeat and completion persistence.

When active calls drain, shutdown returns `True`. `close()` refuses to close the engine while active work remains. The caller can retry close after the active call returns.

## Supervisor contract

The external worker process supervisor should:
1. Stop scheduling new `WorkforceApplicationRuntime.run_once()` calls.
2. Call `runtime.shutdown(timeout_seconds=<configured deadline>)`.
3. If it returns `True`, call `runtime.close()`.
4. If it returns `False`, keep the process and engine alive while active work continues, or deliberately terminate the process. Forced termination relies on lease expiry and startup recovery; it must not trigger blind task replay.
5. Do not close the task engine concurrently with an active call.

The workforce composition does not create its own background worker or signal handler. This phase provides the safe lifecycle primitive for the explicit external supervisor; it does not claim to install a process manager or integrate OS signals.

## Tests and verification

New application-runtime tests cover:
- a bounded shutdown timeout while a run is active;
- the engine remaining usable after timeout;
- rejection of new runs after shutdown begins;
- successful drain and close after the active call returns;
- refusal to close while a run remains active.

The tests are committed but have not been independently executed in this environment. GitHub Actions must complete successfully before this change is treated as verified. No production deployment or graceful process-signal integration is claimed.
