# Parallel specialist worker failure aggregation audit

**Review date:** 2026-11-28  
**Branch:** research/reference-projects-integration-assessment  
**Scope:** `automation/specialist_worker.py:SpecialistTaskWorker.run_concurrently` and worker tests.

## Finding: first failed future aborts batch result collection and hides other worker outcomes

**Severity:** Operational reliability / observability (moderate). No authorization bypass identified.

### Evidence

`run_concurrently()` submits one `drain()` future per worker and stores successful `TaskRecord` values in a shared list protected by `results_lock`. It then iterates over futures in submission order and calls `future.result()`:

```python
with ThreadPoolExecutor(...) as pool:
    futures = [pool.submit(drain, index) for index in range(max_workers)]
    for future in futures:
        future.result()
return tuple(results)
```

A future propagates any uncaught exception from its worker. The first such exception exits the result-collection loop. The executor context manager waits for submitted work during shutdown, so other workers can continue and commit task outcomes, but the method raises instead of returning the accumulated successful `results`. Exceptions from later futures are not explicitly collected or reported.

Most specialist handler exceptions are converted to durable failed task records by `run_once()`. However, exceptions can still escape from claim/store operations or from the failure-recording path itself. The parallel supervisor therefore has a reachable exceptional path even though ordinary handler failures are represented as task records.

The existing parallel tests cover successful concurrent execution and graceful shutdown, but do not inject one worker-level exception while another worker completes successfully.

### Impact and limits

The durable workflow store remains the source of truth, so this does not erase completed task records. It does make the batch call's outcome incomplete: the caller receives an exception without the successful sibling results or a structured account of which workers finished, failed, or were still running when the error occurred. A scheduler or host that treats the batch call as one unit may incorrectly classify the whole batch as failed and require a separate database reconciliation to learn what happened.

This is not evidence that tasks are automatically duplicated or that successful task state is rolled back. The executor waits for its submitted threads; it does not cancel sibling work merely because one future raised.

### Recommended remediation

1. Observe every submitted future, regardless of earlier failures; do not stop collection at the first exception.
2. Return or raise a structured batch outcome containing completed task records and sanitized per-worker failures. Preserve the original exception as a cause where appropriate.
3. Define whether a worker infrastructure failure should stop new claims by sibling workers or allow the bounded batch to drain; implement that policy explicitly.
4. Where a failure occurs after a claim but before a terminal transition can be persisted, report the task ID and current durable state so operators can distinguish queued, running, failed, and interrupted work.
5. Add deterministic fault-injection tests: force one worker's claim/store path to raise while a sibling succeeds; assert all futures are observed, the successful record is surfaced, and the failed worker's task state is discoverable.

### Verification boundary

Source-only review at PR #1346 head `c467272b6be2d2281133b5502e9c9d233cc35431`. No code was executed and no tests were run. This audit documents the finding; it does not implement remediation.
