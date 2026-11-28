# Worker supervisor health callback shutdown-timeout audit

**Status:** confirmed source-level lifecycle defect; remediation not included in this audit commit.

## Finding

A caller-supplied health publisher can prevent `WorkerSupervisor.shutdown(timeout_seconds=...)` from reaching its timeout wait. The supervisor invokes health callbacks synchronously and in-order before starting the timed drain wait. A callback that blocks indefinitely can therefore make shutdown block indefinitely, even when the caller supplied a finite timeout (including zero).

This is distinct from a provider handler that cannot be cancelled: the blocked operation is an optional diagnostics callback, and the timeout contract is bypassed before the supervisor even begins waiting for active cycles to drain.

## Evidence

1. `WorkerSupervisor.__init__()` accepts an arbitrary callable `health_publisher`.
2. `_publish_health()` invokes that callback synchronously. It catches exceptions, but does not bound callback duration or isolate it on a separately managed execution path.
3. `_publish_health_event()` serializes callback execution through `_health_condition`, waiting for its reserved sequence and calling `_publish_health()` while still inside that condition's context.
4. `run_cycle()` reserves a start event, increments `_active_cycles`, and synchronously publishes the `running` event before it enters `_run_cycle()`. If this callback blocks, the cycle remains counted as active even though task processing has not begun.
5. `shutdown()` first marks the supervisor draining and sets the stop event, then synchronously calls `_publish_health_event(..., "draining")`. Only after that callback returns does it call `Condition.wait_for(..., timeout=...)`.
6. The timeout therefore bounds only the final active-cycle wait. It does not bound waiting for an earlier health event or the `draining` callback itself.

A concrete failure sequence is: a cycle reserves and begins publishing `running`; the callback blocks; another thread calls `shutdown(timeout_seconds=0)`; shutdown marks draining but its ordered `draining` publication waits behind the blocked `running` callback. The zero-second timeout is never reached. A callback that blocks specifically on `draining` produces the same result.

## Impact

- Shutdown can hang indefinitely despite a finite timeout argument.
- The caller may be unable to proceed with its shutdown escalation, timeout reporting, or dependency-lifecycle decision.
- Because the callback is optional diagnostics, a stalled health sink can take down the supervisor's lifecycle-control path.
- This does not show that the callback can execute task work or bypass task approval. It is a shutdown availability and contract defect.

## Recommended remediation

1. Do not invoke arbitrary health-publisher code synchronously on the shutdown critical path.
2. Prefer a bounded, non-blocking diagnostics handoff to a separately owned publisher with an explicit queue capacity and drop/coalesce policy. Health must remain observational and must never block claims, cycle admission, or shutdown.
3. If ordered delivery remains required, serialize only enqueue/reservation operations under the condition; do not hold the publication condition while executing external callback code.
4. Define publisher shutdown/flush separately, with its own timeout and explicit result. The worker supervisor's drain timeout must begin before any optional diagnostics operation.
5. Ensure callback re-entry cannot deadlock the ordered publisher mechanism.
6. Add regression tests with a publisher that blocks on `running` and another that blocks on `draining`. Assert that `shutdown(timeout_seconds=0)` returns promptly with `False` while an active cycle remains, then that a later shutdown can return `True` after the cycle and publisher are released. Also test callback re-entry and ordinary callback exceptions.

## Positive controls and scope

- The supervisor marks itself draining and sets the stop event before waiting for active cycles.
- Worker claims use the supervisor condition as a guard, so shutdown cannot pass the drain transition and then allow a new claim through that same in-process guard.
- The specialist worker's finite executor waits for submitted futures to unwind; timeout does not pretend to cancel synchronous provider requests.
- Expired task leases are atomically changed to `interrupted` with a task event. They are not automatically replayed; retry creates a new run with fresh approval state.
- Health publication exceptions are isolated from task-cycle success, but callback latency is not isolated.

This review does not establish a remote exploit or production occurrence. The issue requires a configured health publisher that blocks or re-enters the supervisor.

## Verification limits

Source inspection only at the reviewed PR head. No worker was started, no callback was executed, no task database was opened or modified, and no tests were run locally.
