# Parallel Worker-Pool Exception Propagation Audit

## Scope

Source-only review of:

- `automation/specialist_worker.py`: `SpecialistTaskWorker.run_concurrently()` and `run_once()`
- `automation/worker_supervisor.py`: parallel cycle integration
- `tests/test_specialist_worker.py` and `tests/test_worker_supervisor.py`: existing parallel execution and shutdown coverage

This review did not execute tests or run the application.

## Finding: one worker exception aborts the batch result and hides completed outcomes

**Severity: Moderate reliability / task-lifecycle issue**

### Evidence

`run_concurrently()` creates a bounded `ThreadPoolExecutor`, submits one `drain()` future per worker, and then calls `future.result()` for each future. Each `drain()` repeatedly acquires a task permit and invokes `worker.run_once()`. Successful task records are appended to a shared in-memory results list.

There is no per-task exception boundary in `drain()`. If `run_once()` raises, that worker future completes exceptionally and stops draining. The exception then propagates from `future.result()`, so `run_concurrently()` raises instead of returning the accumulated results tuple. The executor context manager waits for submitted workers to finish during exit, but that does not restore or return the partial results.

The `run_once()` handler catches ordinary `Exception` from execution and attempts to persist a failure transition. However, exceptions from its recovery path can still escape: for example, a store read used to establish current ownership or the subsequent `runtime.fail()` persistence can itself fail. Exceptions from claim or post-claim store operations can also escape. If this occurs after a successful claim and before a terminal transition is committed, the durable task may remain `running` under that worker's lease until normal expiry recovery marks it interrupted.

`WorkerSupervisor._run_cycle()` directly consumes the returned tuple from `run_concurrently()`. It therefore cannot produce a `WorkerCycleReport` for a partially successful batch when one future raises; the cycle itself raises, and its already-completed task records are not surfaced in the report.

Existing parallel tests cover successful concurrent work, task budgets, and orderly shutdown. The inspected tests do not cover one worker future failing while another worker succeeds, nor assert how a post-claim persistence failure is represented to the caller.

### Impact

- A single worker's exceptional exit can turn a partially successful batch into a top-level exception.
- Callers lose the in-memory list of completed task outcomes even though those tasks may already be durably succeeded or failed.
- A task claimed immediately before a persistence/store exception may remain in `running` until its lease expires and a later recovery cycle interrupts it.
- Supervisors and operators receive no structured partial-batch report distinguishing successful, failed, and unresolved tasks.

This is not evidence that the executor abandons its threads: the executor context manager waits for submitted work during teardown. It is also not evidence that tasks are automatically replayed. The identified issue is exception isolation, result visibility, and delayed recovery of a possibly still-running task.

## Recommended remediation

1. Give each claimed task an explicit outcome boundary in the drain loop. Preserve the task ID and worker identity as soon as claim succeeds, and distinguish a terminal persisted result from an unresolved outcome.
2. Do not silently convert persistence uncertainty into a normal task failure. If completion/failure persistence cannot be confirmed, return or record an explicit `outcome_unknown` / unresolved status and rely on ownership-aware reconciliation.
3. Collect every worker future outcome before deciding whether the batch itself failed. Return a structured batch report containing completed records, per-worker errors, and unresolved claimed task IDs; alternatively, raise a typed aggregate exception that carries that report.
4. Ensure one worker's failure does not prevent other workers' results from being surfaced. Keep the existing bounded task permit and stop-event behavior.
5. Preserve the original exception and redact provider/store details from externally visible diagnostics.

## Regression tests

- Run two workers concurrently; let one finish a task successfully while the other raises after claiming a task. Assert the successful result remains available in the batch report.
- Inject a store failure while reading the claimed task's current state or while persisting its failure transition. Assert the claimed task is reported as unresolved rather than silently omitted.
- Inject a claim-time exception in one worker and verify other workers still finish and their outcomes are returned.
- Verify unresolved task ownership and lease expiry/recovery behavior without starting a second execution while the original handler is still active.
- Verify the supervisor returns or exposes a structured partial-cycle outcome and does not misreport a partially successful cycle as wholly unprocessed.

## Boundaries and confidence

The control-flow gap is directly visible in the inspected source. Whether a particular database/provider failure reaches the post-claim unresolved state depends on the failing operation and timing. The audit does not claim that every worker exception strands a task, that a worker thread is abandoned, or that a duplicate execution occurs automatically.

No runtime code was changed. Tests, CI, and live provider/database fault injection were not run.
