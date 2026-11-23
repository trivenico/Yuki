# Yuki Engineering Audit 063 — Application Runtime, Lifecycle Management, and Resource Governance

**Review date:** 2026-11-23  
**Repository:** `trivenico/Yuki`  
**Review type:** Targeted source and test inspection  
**Status:** Code/architecture review; no runtime load test or production shutdown test was executed.

> Scope note: Inspected conversation runtime composition, ASGI lifecycle, web launcher, agent execution loop, durable SQLite task engine, durable skill worker, worker supervisors, resilience/self-healing primitives, and their focused tests. This does not cover every provider, desktop, voice, or platform-specific runtime path.

## Executive assessment

Yuki has several strong lifecycle foundations:
- Shared conversation composition between API and local surfaces.
- Idempotent close paths for conversation runtime and durable task engine.
- ASGI lifespan shutdown support.
- Bounded synchronous agent turns, tool calls, transcript size, and cooperative cancellation checks.
- Durable task leases, heartbeat renewal, idempotency, bounded payloads, and explicit interrupted-task handling.
- Finite worker cycles, bounded parallelism, stop events, and capped exponential backoff.
- Health checks are explicitly registered and read-only; self-healing actions require approval.

The highest-risk gaps are cancellation of synchronous work, heartbeat-loss behavior during side effects, complete ownership/closure of all resources created by web composition, health-check timeout isolation, and lifecycle observability. The existing tests are useful unit/contract tests but do not establish behavior under process termination, stuck providers, repeated deploy shutdown, or resource exhaustion.

## 1. Application composition and ownership

### `core/conversation_application_runtime.py`

Observed:
- Composes a conversation service and selected persistence store.
- Validates required dependencies and owner scope before opening the store.
- Closes the owned store once; construction failure closes the store.

### `api/runtime.py`

Observed:
- `ConversationRuntime` wraps the authenticated API and static web shell.
- Implements ASGI lifespan startup and shutdown.
- Delegates shutdown to the shared conversation application runtime.

Assessment:
- The composition boundary is a good direction: service construction and store lifecycle are not duplicated across API/CLI surfaces.
- The application runtime owns the conversation store, but the web composition creates additional long-lived dependencies (auth service, provider adapter/manager, device pairing, inference jobs, income store, and optional registries/services). Their ownership and close semantics must be explicit.
- Inspect the full runtime builder and every injected dependency to determine which resources own open connections, threads, subprocesses, sessions, or caches. Do not assume the conversation store's close closes them.

Required:
1. Define an application resource registry/container with explicit ownership and reverse-order shutdown.
2. Require resources to expose a consistent `close()` or `aclose()` contract.
3. Make shutdown idempotent and continue closing remaining resources if one close fails; aggregate sanitized failures.
4. Add startup rollback: if resource N fails to initialize, close resources 1..N-1.
5. Ensure no resource is accidentally closed by a component that does not own it.
6. Document lifecycle for CLI, API, desktop, and worker entrypoints independently.

## 2. ASGI lifespan and web server

Observed:
- `scripts/run_web.py` builds the runtime and starts Uvicorn with lifespan enabled.
- `ConversationRuntime` handles ASGI lifespan startup and shutdown and closes its application runtime.
- Web binding defaults to loopback and requires explicit public-bind opt-in.

Recommendations:
- Add explicit startup readiness: report startup complete only after mandatory dependencies and resources have initialized successfully.
- Ensure a startup exception unwinds all partially created dependencies.
- On shutdown, stop accepting new work, signal background workers, wait for bounded in-flight work, close providers/clients, then close stores.
- Handle repeated shutdown signals and shutdown timeouts deterministically.
- Test SIGTERM and container termination with active requests, database writes, model calls, and queued tasks.
- Keep liveness separate from readiness; liveness should not fail merely because an optional provider is unavailable.

## 3. Agent execution and cancellation

### `core/agent_execution_loop.py`

Observed:
- Synchronous execution loop with bounds on model turns, tool calls, transcript size, and total configured duration.
- Checks cancellation before turns and before tool dispatch.
- Documents that adapters must enforce their own network timeouts.

Important limitation:
- The duration budget and cancellation checks are cooperative. They cannot interrupt a synchronous model/provider call or a tool callback already in progress. If a provider call hangs, the loop cannot enforce its own duration limit until control returns.

Recommendations:
1. Enforce connect/read/total deadlines in every provider adapter and tool integration.
2. Prefer async/cancellable interfaces for long-running work where supported.
3. For operations that cannot be safely interrupted, isolate them in supervised subprocesses with explicit termination and cleanup semantics.
4. Propagate one cancellation/deadline context from HTTP request to agent loop, provider call, tool invocation, and persistence.
5. Re-check cancellation and authorization immediately before every side effect.
6. Record distinct outcomes for user cancellation, deadline, provider timeout, tool timeout, and shutdown.
7. Ensure cancellation does not mark a task complete while an external side effect may still be running.

## 4. Durable task engine and worker leases

### `core/durable_task_engine.py`

Observed:
- SQLite-backed transactional task queue with idempotency, dependencies, state checks, lease fields, event journal, bounded JSON payloads, and explicit close.
- Uses per-operation connections, foreign keys, busy timeout, and immediate write transactions.
- Documents that SQLite is for a local filesystem and is not a distributed database.

Recommendations:
- Keep SQLite queue deployment to a single-host/local-filesystem model unless an explicitly supported coordination design is introduced.
- Add database schema version tracking and migration tests (also raised in Audit 061).
- Add process-crash and disk-full tests around claim, heartbeat, completion, and event insertion.
- Ensure lease owner and lease expiry are checked atomically on every state transition.
- Define clock-skew tolerance and use a consistent authoritative clock strategy across workers.
- Bound event retention and task payload/result retention; define sensitive-data redaction.
- Ensure task execution side effects use idempotency or reconciliation because a database lease cannot provide exactly-once external execution.

### `core/durable_skill_worker.py`

Observed:
- Supports only the fixed `skill.invoke` task type.
- Requires single-attempt policy for side-effecting skills.
- Validates exact payload shape and bounded fields.
- Starts a daemon heartbeat thread during synchronous skill invocation.
- On heartbeat failure, the heartbeat thread records the error and exits; after the skill invocation returns, the worker raises and reconciles durable task state.

Critical lifecycle gap:
- A heartbeat failure does not cancel or terminate the in-progress synchronous skill invocation. The task may continue performing external side effects after its lease is lost and potentially after another worker has recovered the task. Single-attempt policy prevents automatic retry in this worker, but does not stop the already-running operation or prevent external duplicate effects caused by other recovery paths.

Required:
1. Pass a cancellation token into the skill runtime and require long-running skills to check it before side effects.
2. For non-cooperative skills, execute in a process boundary that can be terminated after lease loss.
3. Use fencing tokens/monotonic lease generations at downstream side-effect boundaries where possible.
4. Require external idempotency keys for actions that can be safely deduplicated.
5. Mark the task outcome as uncertain when a side effect may have occurred but lease ownership was lost; require reconciliation before resubmission.
6. Add tests where heartbeat fails while a skill is blocked, then verify that the skill cannot continue committing side effects.

## 5. Worker supervisors and backpressure

### `automation/worker_supervisor.py`

Observed:
- Processes a finite, bounded task batch.
- Bounds recovery count and concurrency (up to 16 workers).
- Recovers expired leases before processing new work.
- Does not create a perpetual thread/process itself.

### `automation/inference_worker_supervisor.py`

Observed:
- Runs a stop-event-controlled loop.
- Uses bounded idle polling and capped exponential backoff.
- Stops after device authentication failure or consecutive transient-error limit.
- Tracks basic counters in a returned stats object.

Recommendations:
- Define a single service supervisor responsible for starting/stopping worker loops and joining every worker thread/process.
- Add queue depth, oldest task age, active workers, lease-renewal failures, cancellation latency, retries, and dead-letter metrics.
- Add backpressure controls based on queue depth, provider capacity, tenant quotas, and database pool saturation.
- Ensure shutdown stops claiming new work before waiting for in-flight tasks.
- Add jitter to retry backoff to avoid synchronized retry storms.
- Ensure error logs are sanitized: the inference supervisor currently uses exception logging, which may include exception messages and traceback-local context. Review logger configuration and redact provider credentials, request data, and private paths.
- Distinguish permanent failures from transient failures rather than retrying every generic exception.

## 6. Health checks and self-healing

### `core/resilience.py`

Observed:
- Health checks are explicitly registered, bounded in count, run read-only, and sanitize exception messages.
- Checks execute sequentially in the caller's thread.

Gap:
- There is no per-check timeout. A blocked check can stall the entire health report or readiness request indefinitely.

Recommendations:
- Define check classes: liveness, readiness, dependency health, and deep diagnostics.
- Give each check a strict deadline and total report deadline.
- Isolate potentially blocking checks so they cannot stall the server event loop or request worker.
- Cache deep diagnostics and avoid running expensive probes on every request.
- Return only bounded, non-sensitive details.
- Keep optional integrations degraded rather than failing liveness.

### `core/self_healing.py`

Observed:
- Recovery proposals are read-only.
- Execution requires an approval callback; missing approval provider fails closed.
- Registered callbacks are bounded and errors are sanitized.

Recommendations:
- Bind approval to an immutable action/resource/argument digest, requester, expiry, and one-time approval ID.
- Add timeout and cancellation requirements to callbacks.
- Require each recovery action to define preconditions, backup/rollback behavior, and a postcondition check.
- Persist proposal, approval, execution, and outcome audit events.
- Prevent duplicate concurrent execution of the same recovery action unless explicitly safe.

## 7. Resource governance

Adopt explicit budgets for:
- Per-request wall-clock duration and cancellation latency.
- Provider connection/read/total timeout and concurrent calls.
- Per-worker concurrency, task batch size, and lease duration.
- Threads/processes/subprocesses per service.
- Memory, CPU, file descriptors, open DB connections, and disk usage.
- Queue depth, task payload/result size, and event retention.
- Shutdown grace period and forced-termination deadline.

Use OS/container controls as the final enforcement boundary. In-process limits alone cannot contain a stuck or memory-exhausting native dependency.

## 8. Test and verification plan

Existing focused tests include:
- `tests/test_conversation_application_runtime.py`: shared composition, repeated close, dependency validation.
- `tests/test_durable_skill_worker.py`: fixed task type, approvals, bounded payload, single attempt, lease heartbeat.
- `tests/test_worker_supervisor.py`: bounded cycles, parallel work, expired lease recovery.
- `tests/test_inference_worker_supervisor.py`: stop event, backoff, authentication failure.
- `tests/test_resilience_self_healing.py`: read-only health checks, sanitized errors, approval gate.
- `tests/test_agent_execution_loop.py`: model/tool budgets, cancellation checks, transcript limits.

Add:
1. Heartbeat loss while a side-effecting skill is blocked.
2. SIGTERM during provider call, tool execution, database commit, and task completion.
3. Startup failure at every resource-construction step; assert all earlier resources close.
4. One resource close failure does not prevent the rest from closing.
5. Health check that hangs, times out, or exhausts its worker.
6. Provider ignores timeout; verify process-level termination boundary.
7. Worker stop during idle, claim, execution, and lease renewal.
8. Queue saturation and provider capacity backpressure.
9. Concurrent shutdown calls and repeated close.
10. Memory/FD/thread/process leak test across repeated start/stop cycles.
11. Lease loss plus stale worker completion attempt is rejected.
12. Shutdown deadline expiry yields a visible, bounded forced-stop result.

## 9. Prioritized remediation

| Priority | Work item | Evidence required |
|---|---|---|
| P0 | Make heartbeat loss stop or fence in-flight side effects | Heartbeat-loss integration test |
| P0 | Enforce provider/tool hard timeouts and cancellation | Stuck-call termination tests |
| P0 | Define complete runtime resource ownership and reverse-order close | Startup rollback and shutdown tests |
| P1 | Add health-check deadlines and isolation | Hung-check tests |
| P1 | Add worker lifecycle orchestration and shutdown drain | SIGTERM integration tests |
| P1 | Add resource/backpressure metrics and quotas | Load and saturation tests |
| P1 | Sanitize exception telemetry in workers | Secret-redaction tests |
| P2 | Add leak detection and repeated lifecycle soak tests | Thread/FD/memory trend report |
| P2 | Bind recovery approval to exact action and resource | Replay/tamper tests |

## Verification status

This audit is based on targeted source and test inspection. No tests were executed, no load/soak tests were run, no SIGTERM experiment was performed, and no stuck provider or lease-loss fault was injected. Production resource limits, process supervision, logging redaction, and worker deployment topology remain unverified.
