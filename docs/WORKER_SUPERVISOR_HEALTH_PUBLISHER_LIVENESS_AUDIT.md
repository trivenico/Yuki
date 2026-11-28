# Worker supervisor health-publisher lifecycle audit

**Review date:** 2026-11-28  
**Branch:** research/reference-projects-integration-assessment  
**Scope:** `automation/worker_supervisor.py`, `automation/specialist_worker.py`, and `tests/test_worker_supervisor.py`.

## Finding: synchronous health publication can indefinitely block cycle admission and shutdown

**Severity:** Availability / lifecycle reliability. Requires a configured health publisher that blocks or raises a process-control exception.

### Evidence

`WorkerSupervisor.run_cycle()` increments `_active_cycles` and reserves a health-event sequence while holding `_condition`. It then calls `_publish_health_event(..., "running")` **before entering the try/finally that decrements `_active_cycles`**.

`_publish_health_event()` waits synchronously for its reserved sequence and invokes the configured `health_publisher` inline. `_publish_health()` catches ordinary `Exception` from the callback, but it cannot bound a callback that never returns and does not catch `BaseException` process-control exceptions.

The shutdown path sets `_draining` and `_stop_event`, reserves its own health sequence, then synchronously publishes the `draining` event **before** it starts the timeout-bounded wait for `_active_cycles == 0`. Because health events are serialized by sequence, a stalled earlier publisher also prevents the shutdown event from reaching its callback.

Consequently:
- A blocked `running` publication leaves the cycle counted active before task execution begins.
- `shutdown(timeout_seconds=...)` can block inside health publication beyond its requested timeout, before reaching the timed condition wait.
- If the callback raises `KeyboardInterrupt` or `SystemExit` during the initial publication, execution bypasses the cycle's decrementing `finally`, leaving `_active_cycles` elevated and later shutdown unable to drain.

Existing tests prove that an ordinary `OSError` raised by the publisher does not fail a cycle, and that normal concurrent publications preserve sequence order. They do not cover a blocked publisher or a process-control exception at the initial publication boundary.

### Impact and limits

This is a liveness defect in the optional diagnostics integration, not a task authorization bypass. It does not imply that a default deployment configures a blocking publisher. The configured callback is injected by the host, so practical impact depends on its implementation and whether it can block on network, disk, or another service.

### Recommended remediation

1. Put the active-cycle accounting under a `try/finally` that begins immediately after incrementing it, including the initial health event.
2. Keep health publication out of the shutdown critical path. Shutdown's timeout must bound the complete operation, not only the final condition wait.
3. Make diagnostics asynchronous with a bounded queue and explicit drop/backpressure policy, or use a bounded callback contract that cannot stall lifecycle transitions.
4. Ensure sequence ordering cannot let one missing/stalled publisher permanently block all later events; use timeout/skip semantics or a dedicated publisher worker.
5. Treat diagnostics as best-effort for all callback failures while preserving cleanup of supervisor accounting even for `BaseException`.
6. Add deterministic tests with an Event-blocked publisher, a shutdown timeout shorter than the block, and a publisher that raises `KeyboardInterrupt`; verify active-cycle accounting returns to zero and shutdown remains bounded.

### Verification boundary

This is a source-level finding from PR #1346 head `4d412f598e90cb8f7dcc80c82f014246cea88c41`. No supervisor was run, no callback was blocked in a live process, and no tests were executed. The finding is documented only; no runtime remediation is included.
