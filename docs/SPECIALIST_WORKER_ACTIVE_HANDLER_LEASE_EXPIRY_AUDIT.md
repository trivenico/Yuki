# Specialist worker lease expiry during active handler audit

## Scope

Source review at PR #1346 head `c30c268480145d5a0182ee2bdbed23c49bcab5cd`. Reviewed:

- `automation/worker_supervisor.py`
- `automation/specialist_worker.py`
- `automation/workflow_store.py`
- `automation/task_runtime.py`
- `automation/local_worker_runtime.py`
- `tests/test_worker_supervisor.py`

This is a source-level audit. Tests were not executed.

## Finding: an active handler can outlive its lease and become retryable

**Severity: Moderate (P2)**

### Evidence

1. `WorkflowRunStore.claim_next()` defaults to a 60-second lease and persists `lease_owner` plus `lease_expires_at` when changing a queued task to running.
2. `WorkflowRunStore.renew_lease()` exists and correctly uses worker identity, expected version, and an unexpired-lease check.
3. `SpecialistTaskWorker.run_once()` claims the task, checks execution authorization, and invokes the handler synchronously. There is no lease renewal or heartbeat around that handler call.
4. After the handler returns, the worker checks that the task remains running and owned by its worker ID before completing it.
5. `WorkerSupervisor._run_cycle()` calls `TaskRuntime.recover_expired()` before processing the next batch. Expired running leases are changed to `interrupted`; recovery intentionally does not replay them.
6. `TaskRuntime.retry_failed()` permits explicitly retrying interrupted work by creating a distinct new run with a fresh approval state. It does not establish that the original process or synchronous handler has exited.
7. `run_cycle()` can be invoked concurrently, and a separate supervisor/process can also perform recovery. The in-process shutdown drain only coordinates cycles within one supervisor instance.

### Impact

If a provider call or injected handler runs longer than the 60-second lease, a concurrent or separate supervisor can mark the task interrupted while the original handler is still executing. An authorized retry can then create a second run and dispatch the same logical work before the first handler returns.

The built-in local specialist handlers are bounded text-generation handlers without shell or workspace side effects, which limits the current local composition's impact mainly to duplicate provider work, cost, and competing outputs. However, `SpecialistTaskWorker` accepts injected callables, so the worker abstraction itself does not guarantee side-effect-free handlers. For handlers with external side effects, retry overlap can duplicate those effects.

The original worker's post-handler ownership check prevents it from completing a task after recovery has changed its state. It does not stop the original handler or prevent a distinct retry run from executing concurrently.

### Existing safeguards and limits

- Claims are atomic and use unique worker identities in concurrent pools.
- Lease renewal is compare-and-swap protected and rejects already-expired leases.
- Recovery marks work interrupted rather than automatically replaying it.
- Completion/failure is tied to the worker ID and expected version.
- Shutdown prevents new claims within the same supervisor and waits for active cycles; a timeout is explicitly not treated as cancellation.
- These controls do not keep the lease alive during a long-running synchronous handler and do not establish cross-process handler liveness before allowing retry.

### Recommended remediation

1. Add a lease heartbeat for the entire handler lifetime. It must renew before expiry and stop promptly when the handler exits.
2. Coordinate the heartbeat's updated task version with completion/failure CAS operations; do not let a renewal silently invalidate the worker's final expected version.
3. If renewal fails because the lease expired, ownership changed, or the task was cancelled, signal loss of authority to the handler where supported and prohibit any subsequent side-effecting step. For synchronous providers that cannot be interrupted, keep the run non-retryable/ambiguous until the handler's process is known to have exited.
4. Make recovery distinguish a genuinely abandoned worker from an active but slow worker. A bounded lease extension/grace period can reduce false recovery but is not a substitute for a heartbeat or process-liveness protocol.
5. Gate retry of recovered tasks on a durable terminal execution/liveness disposition, or explicitly expose an `outcome_unknown` state requiring reconciliation for handlers that may have external side effects.
6. Add deterministic regression tests with a handler blocked beyond the lease duration, a concurrent recovery attempt, and an attempted retry. Assert that the second execution cannot begin until the first is known to have stopped. Also test heartbeat renewal, lease-loss races, cancellation during heartbeat, and heartbeat shutdown/join.

### Non-findings

- No evidence was found that expired tasks are automatically replayed.
- The current supervisor shutdown path correctly returns `False` on timeout and documents that provider/store dependencies must remain alive.
- The current local runtime revokes its ephemeral approval session before draining.
- The identified issue is specifically lease lifetime during an active handler and retry eligibility after recovery, not the supervisor's stop-event/claim-lock ordering.

## Audit limitations

This review inspected source and existing test definitions only. No tests, runtime experiments, or CI jobs were executed as part of this audit. The finding should be validated with the proposed deterministic concurrency tests before release.
