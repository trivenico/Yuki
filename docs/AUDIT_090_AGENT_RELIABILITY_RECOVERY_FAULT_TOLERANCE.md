# Audit 090 — Agent Reliability, Fault Tolerance, Recovery, and Self-Healing

Repository: trivenico/Yuki  
Base: main  
Scope: durable workflow tasks, task retry/recovery, paired-device inference jobs, local inference worker, worker supervisor, agent lifecycle journal.  
Outcome: source audit; no runtime patch submitted.

## Executive summary

Yuki's durable workflow-task path deliberately avoids automatic replay after worker lease expiry. Expired work is atomically marked interrupted and journaled; a separately authorized retry creates a new task and requires fresh approval for approval-gated definitions. This is the safer default for workflows that may have external side effects.

The paired-device inference path makes a different tradeoff: an expired running job is automatically returned to queued during the next device claim. Lease-token rotation and hash verification fence stale completion, but cannot terminate the original provider request. A replacement worker may therefore execute the same inference while the old provider call is still running. The inspected job schema does not track attempts, retry schedule, or a dead-letter state.

The inference supervisor uses bounded exponential backoff for exceptions, but all counters and backoff state are in memory and reset on process restart. This audit identifies recovery semantics that need a unified, explicit policy. It does not demonstrate an actual production duplicate execution or outage.

## Files inspected

- automation/task_runtime.py
- automation/workflow_store.py
- automation/agent_task_journal.py
- automation/inference_worker_supervisor.py
- automation/local_inference_worker.py
- core/inference_jobs.py
- tests/test_task_runtime.py
- tests/test_agent_task_journal.py
- tests/test_inference_jobs.py

## Positive controls observed

1. Workflow task creation is idempotent by key and request digest; conflicting reuse of a key is rejected.
2. Workflow claims are atomic and serialize competing workers.
3. Task transitions and task-event journal insertion can be committed in one transaction.
4. Worker completion and lease renewal check the current worker ID, lease expiry, and expected version.
5. Expired workflow leases are atomically converted to interrupted tasks with a task.interrupted event. The recovery path explicitly does not replay work.
6. Retry is a separate authorized operation and creates a new task using a stable key derived from source task ID and observed source version. Repeating the same retry request is idempotent.
7. Approval-required retries do not inherit prior approval; the new task requires fresh approval before claim.
8. AgentTaskJournal persists bounded lifecycle metadata and checks the active worker lease before checkpointing.
9. Inference job claims use a cryptographically random lease token; only its SHA-256 digest is persisted. The token is excluded from public serialization and repr.
10. Inference completion checks device authentication, device/job binding, active status, lease expiry, and constant-time lease-token digest comparison.
11. Cancellation clears the lease token digest and prevents a late worker completion from committing.
12. Inference supervisor has bounded exponential backoff for exceptions and a maximum consecutive-error circuit stop; it does not start its own background thread or process.

## Findings

### P1 — Expired inference jobs are automatically requeued without attempt limits or backoff

In InferenceJobService.claim_next, every expired running job for the authenticated device is changed back to queued, its lease token digest is cleared, and it becomes eligible for immediate claim. The job schema has no attempt counter, maximum-attempt policy, next-retry timestamp, or dead-letter status. The claim path can therefore repeatedly requeue the same job after successive lease expirations.

Risk: a persistently failing or slow provider request can be executed repeatedly without a bounded retry budget or operator quarantine. Multiple attempts may consume substantial compute and provider resources.

Required direction: add durable attempt count, configurable maximum attempts, next-attempt timestamp, bounded exponential backoff with jitter, and terminal dead-letter/quarantined state. Make claim eligible only when retry time has arrived and attempt budget remains. Preserve explicit owner cancellation and operator requeue semantics.

### P1 — Lease fencing prevents stale result commits but cannot stop the old inference computation

When a job lease expires, a new worker can claim it with a rotated lease token. The old worker's completion is rejected, but LocalInferenceWorker invokes a synchronous provider generation call with no cancellation handle or lease heartbeat. The original provider computation may continue after the job has been reclaimed.

The constructor currently requires the lease to exceed provider timeout by 15 seconds, which reduces but does not eliminate risk from scheduling pauses, network behavior, process suspension, clock differences, or timeout mismatch.

Risk: duplicate concurrent inference, wasted GPU/CPU capacity, and ambiguous provider-side billing or external side effects if provider semantics change.

Required direction: propagate an absolute deadline and cancellation signal to the provider; renew leases for long-running work; stop/reap local processes on lease loss; and distinguish logical fencing from confirmed physical termination. Never assume lease expiry kills computation.

### P1 — Retry and recovery policy differs between workflow tasks and inference jobs

Workflow tasks become interrupted and require an explicit retry, while inference jobs automatically requeue on lease expiry. This inconsistency means similar-looking autonomous tasks can have materially different replay semantics.

Risk: orchestration layers may assume one global recovery policy when underlying task types behave differently. An automatic retry safe for pure inference may be unsafe for a task that performs an external action.

Required direction: classify task side-effect semantics explicitly (pure/idempotent, idempotency-key protected, or non-replayable). Require an explicit recovery policy per task type. Non-idempotent side effects must not be automatically replayed; safe retry requires a stable external idempotency key or reconciliation with the remote system.

### P2 — Supervisor backoff state is process-local and lost on restart

InferenceWorkerSupervisor.run_forever tracks consecutive errors in local variables. Its exponential delay is bounded, and it stops after a configured consecutive-error limit, but a restart resets the counter and backoff state. It does not persist a circuit-breaker state or fleet-level health status.

Risk: crash/restart loops can repeatedly restart at the shortest delay, and multiple worker processes can independently retry a failing dependency.

Required direction: persist dependency health/circuit state or coordinate it through a shared supervisor; add jitter; expose circuit-open and recovery telemetry; define operator reset and half-open probe behavior. Ensure process restarts do not bypass the fleet's retry budget.

### P2 — Job attempts and failure classes are not first-class operational data

Inference jobs store queued/running/succeeded/failed/cancelled status and a stable error code, but no attempt history or structured failure class distinguishing transient provider outage, permanent invalid model, policy denial, expired lease, cancellation, and worker crash.

Risk: operators cannot reliably distinguish retryable failures from permanent failures or determine how much repeated work has occurred.

Required direction: record per-attempt metadata (worker/device, claim time, lease expiry, sanitized failure class, completion time) with bounded retention. Do not persist prompts, raw provider exceptions, credentials, or sensitive outputs in telemetry.

### P2 — Recovery observability and operator controls are incomplete

Workflow recovery produces durable interrupted events and authorized retry operations. Inference job expiry/reclaim is embedded in claim logic and has no dedicated event history or dead-letter inspection/replay interface in the inspected service.

Risk: repeated expiry and reclaim may be difficult to diagnose; operators lack a clear quarantine and controlled recovery workflow.

Required direction: persist metadata-only attempt and recovery events, expose owner/admin-scoped dead-letter inspection, require explicit authorization for manual requeue, and audit every operator action.

### P2 — Recovery correctness needs multi-process and crash-point testing

The inspected tests cover expired lease reclaim, stale token rejection, cancellation fencing, task recovery primitives, idempotent retry, and atomic task event rollback. They do not establish behavior under actual process termination at every claim/provider/commit boundary, multi-worker races, or a provider call that outlives its lease.

Required direction: add fault-injection tests for crash after claim, crash during provider call, lease expiry during execution, simultaneous reclaim, cancellation racing completion, repeated failure reaching dead-letter, restart during backoff, and stale completion after requeue. Run them against the production database/worker topology.

## Recommended recovery state model

For replay-safe inference jobs:

1. queued
2. running (attempt N, lease epoch/token, absolute deadline)
3. retry_wait (bounded retry time, attempt budget remaining)
4. succeeded / failed-permanent / cancelled
5. dead_letter (attempt budget exhausted or operator quarantine)

For side-effecting agent tasks, preserve the safer interrupted-and-explicit-retry model unless the action has a verified idempotency/reconciliation contract. A retry must create a new attempt identity and never reuse stale worker authority.

## Implementation sequence

1. Define per-task replay classification and explicit retry policy.
2. Add durable inference attempt count and bounded retry scheduling.
3. Add dead-letter state, inspection, and authorized manual requeue.
4. Add provider cancellation/deadline/lease renewal and physical termination confirmation.
5. Persist supervisor circuit state and fleet-wide backoff coordination.
6. Add attempt/recovery event telemetry with sensitive-data redaction.
7. Add crash-point, multi-worker, and duplicate-side-effect fault-injection tests.

## Limitations and test status

This audit is based on source inspection of the paths listed above. No tests were executed, no live provider/worker failure was induced, no duplicate production inference was demonstrated, and no deployment topology was inspected. Existing tests were inspected as evidence of intended behavior; they are not represented as passing.

No runtime patch was submitted. Adding retry fields alone would not safely address the physical cancellation and replay semantics of provider calls. The next implementation should coordinate job schema, claim scheduling, attempt history, worker deadlines, API responses, and regression tests.
