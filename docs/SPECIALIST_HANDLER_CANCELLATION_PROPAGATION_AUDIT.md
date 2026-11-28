# Specialist Handler Cancellation and Stop-Event Propagation Audit

## Scope

Source-only review at PR #1346 head `3b4d078c687ef55d776c017b0ff09fb326280f98`.

Reviewed:

- `automation/specialist_worker.py`
- `automation/task_runtime.py`
- `automation/workflow_store.py`
- `automation/worker_supervisor.py`
- `tests/test_specialist_worker.py`
- `tests/test_worker_supervisor.py`

No tests or live provider calls were executed.

## Finding: task cancellation does not interrupt an in-flight specialist/provider operation

**Severity: Moderate reliability and operator-control limitation**

### Evidence

1. `WorkflowRunStore` permits a running task to transition directly to `cancelled`. `TaskRuntime.cancel()` commits that transition and clears the task lease as part of the state update.
2. `SpecialistTaskWorker.run_once()` invokes the registered handler synchronously as `handler(argument)`. Its handler contract accepts only the task argument; it has no cancellation token, stop event, deadline, or operation handle.
3. The worker checks `stop_event` only before claiming a task. The event is not checked during the synchronous handler invocation and is not passed into provider handlers.
4. After the handler returns, `run_once()` reads the durable task state. If it is already `cancelled`, the worker returns the cancelled record and discards the provider output. This prevents late completion from overwriting cancellation, but it does not stop the operation that produced that output.
5. The existing `test_cancellation_during_handler_prevents_late_completion` explicitly waits for the slow handler to return after cancellation, then verifies that the task remains cancelled. It tests state consistency, not interruption of the handler/provider call.
6. `WorkerSupervisor.shutdown()` sets its stop event and waits for active cycles. Its timeout documentation correctly notes that a synchronous provider call may not be cancellable and that a timeout does not make it safe to close the task store/provider beneath the handler.

### Impact

A task can be shown as `cancelled` while its synchronous provider operation is still active. The operation may continue consuming latency and provider resources, and may incur usage charges, even though its result will not be committed. A stop request similarly prevents subsequent claims but does not interrupt a handler already running.

This creates a mismatch between the task's terminal state and the actual execution state. It also means shutdown cannot guarantee prompt interruption of provider work; it can only wait up to its configured timeout and report that draining did not complete.

### Boundaries

The inspected specialist handlers are text-generation handlers and are documented as text-only; this review does not claim they execute arbitrary shell commands, modify the workspace, or perform arbitrary external side effects. The confirmed issue is that a synchronous inference operation cannot be interrupted through the current handler contract. Whether a particular provider transport supports cancellation is provider-specific and was not verified here.

The existing code deliberately prevents a late handler result from changing a cancelled task. That is a useful consistency guard and should be preserved.

## Recommended remediation

1. Introduce an explicit cancellation context/token for each claimed task. Pass it through the specialist handler interface and provider adapter boundary.
2. Where supported, implement provider-specific request cancellation (for example, closing/cancelling the underlying HTTP request or stream). Do not claim that setting a Python event cancels a blocking network call unless the transport actively observes it.
3. Separate `cancel_requested` from `cancelled` when the operation has not acknowledged termination. Only publish the terminal cancelled state after the handler has exited or the provider operation has confirmed cancellation. If termination cannot be confirmed, expose an unresolved/cancellation-pending state and retain truthful execution metadata.
4. Preserve the current post-handler state/ownership check so late results can never overwrite cancellation or another terminal transition.
5. Propagate supervisor stop requests through the same cancellation context for active tasks, while keeping shutdown timeout behavior explicit: a timeout must not trigger premature provider/store teardown.
6. Add deterministic tests with a cancellable fake provider: cancellation must reach the provider, the provider must acknowledge stopping, the task must transition to cancelled only under the defined acknowledgement policy, and no late result may be committed. Also test a non-cancellable provider and verify that the system reports cancellation pending rather than falsely claiming execution has stopped.

## Conclusion

The durable task state transition is concurrency-safe against late completion, but the current API cannot stop an already-running synchronous handler. This is an operator-control and resource-usage limitation, not evidence of an authorization bypass or duplicate execution.

No runtime code was changed. Tests, CI, and live provider cancellation behavior were not exercised.
