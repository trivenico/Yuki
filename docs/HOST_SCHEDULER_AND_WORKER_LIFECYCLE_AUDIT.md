# Host Scheduler and Worker Lifecycle Follow-up

**Review date:** 2026-11-26
**Reviewed branch head before this document:** `14ff9a3cc486ca1f469fa05ec1a0c90ed1d5c338`
**Scope:** Targeted source and repository-tree inspection of worker supervisors, durable skill execution, reminder delivery, the proactive notification outbox, and the checked-in systemd example. This does not prove the absence of host-level deployment configuration outside the repository.

## Findings

### 1. There are multiple worker lifecycle models

- `WorkerSupervisor` is a finite-cycle API. It recovers expired workflow leases and processes a bounded specialist batch. It starts no background thread or process; a host scheduler must call `run_cycle()`.
- `InferenceWorkerSupervisor` exposes `run_forever()` with an injected stop event, bounded idle polling, exponential error backoff and a consecutive-error stop condition. The module itself starts no thread/process/timer/listener. Its host must own the thread/process, signal the stop event, join it, and close the worker's underlying resources.
- `DurableSkillWorker` performs one task per `run_once()`. During a skill invocation it creates a daemon heartbeat thread to renew the task lease, then signals and joins it on the normal/exceptional return path. The join has a timeout; a blocked heartbeat operation can therefore outlive the invocation's join window. A maintenance/shutdown contract must account for both the invocation and its heartbeat thread.
- `SpecialistTaskWorker` uses a bounded `ThreadPoolExecutor` only for a finite batch. It has no persistent worker loop or stop event; exiting the executor context waits for submitted futures. Its provider call is not cancellable through the worker API and the inspected specialist worker does not renew the lease during the call.
- These are separate execution systems with different stores and lease semantics. A single generic worker-stopped flag is not sufficient.

### 2. Repository deployment example does not schedule these workers

The checked-in `deploy/systemd/yuki-host-runtime-inventory.service.example` runs `core.host_runtime_inventory_service_cli`. It is a hardened example for the host inventory agent, not a unit for the workflow supervisor, inference supervisor, specialist worker, reminder queue or proactive outbox. Its presence must not be cited as proof that the task/notification workers are deployed or supervised.

No repository-owned scheduler/daemon entrypoint for `WorkerSupervisor.run_cycle()`, `InferenceWorkerSupervisor.run_forever()`, `ProactiveAlertStore.claim_notification()` or `ReminderQueue.due()` was established in this targeted pass. A full search of external deployment manifests, operator scripts and runtime composition remains necessary before asserting no host scheduler exists.

### 3. Reminder queue is a separate JSON-backed delivery boundary

- `companion.proactive.ReminderQueue` stores reminders in a JSON file, using a process-local `RLock` plus the shared cross-process `file_lock()` around read/modify/write.
- `due()` marks due reminders delivered and persists the JSON file before returning the ready items. It does not itself send a notification or run an action.
- This is a separate persistent store from `ProactiveAlertStore` and its SQLite outbox. Its delivery caller and shutdown semantics were not established here.
- Backup must preserve the reminder file and avoid treating marked-delivered as proof that an OS notification or external message was actually delivered.

### 4. Proactive notification outbox remains unconsumed in the inspected tree

- `ProactiveAlertStore` provides atomic claim and lease-token settlement, but the module explicitly does not poll or deliver.
- No first-party caller of `claim_notification()` or `settle_notification()` was found in the inspected repository-tree/source pass. Therefore do not claim end-to-end notification delivery, retries, dead-letter handling, or dispatcher shutdown behavior.
- `observe()` commits the cooldown timestamp and pending outbox record together. This prevents duplicate due decisions for the same observation window, but delivery still depends on a future/host consumer.
- A future dispatcher must use provider idempotency where available. A lease expiring while an external provider call is still in flight can otherwise produce duplicate sends; the local lease token cannot fence a remote notification provider unless the provider supports an idempotency/fencing key.

## Canonical writer inventory additions

| Component | Persistent state | Writer / execution model | Shutdown / maintenance requirement |
|---|---|---|---|
| Workflow specialist tasks | SQLite workflow/task/event database | Finite-cycle supervisor; optional bounded thread pool | Stop new cycles; wait for every submitted future/provider call; prevent stale workers from completing after maintenance generation changes |
| Local inference jobs | Pairing/inference SQLite database | Explicit long-running supervisor loop with stop event and backoff | Signal stop, wait for current run_once() to finish, then close worker-owned resources and DB services |
| Durable skill tasks | Legacy durable task SQLite database | One-shot worker invocation plus daemon lease-heartbeat thread | Stop claims; cancel or bound skill execution; join heartbeat; retain single-attempt/no-automatic-replay semantics for uncertain side effects |
| Proactive notification outbox | Separate SQLite database | Claim/settle API exists; dispatcher not located | No delivery guarantee until consumer is identified; future consumer needs stop/drain, idempotency and restore replay policy |
| Reminders | JSON file | Explicit due() call; cross-process file lock on read/modify/write | Stop due polling and external delivery caller; snapshot under same file-lock protocol; reconcile delivered marker vs actual delivery |

## Required next steps

1. Inspect the full deployment tree, CLI command registry, desktop background services and host runtime composition for the actual invocation points of each supervisor and due/dispatch method.
2. Identify all constructors and close methods for the local inference worker, durable skill engine, workflow store, reminder queue and notification delivery adapters.
3. Define shutdown as a top-level owner operation: stop accepting work, stop scheduler ticks, stop new claims, drain in-flight provider/skill/notification calls, join threads, close pools/stores, then release the maintenance lease.
4. Make each worker/side-effect boundary validate a durable maintenance generation before claim and again before committing or invoking an irreversible target. A DB lease alone cannot stop an already-issued external request.
5. Include reminder JSON and the proactive-alert SQLite outbox as separate stores in the storage manifest. Keep their replay/notification policy explicit during restore.

## Readiness

This pass adds verified worker models and a reminder JSON store to the inventory, but does not identify every external host scheduler or notification caller. No code paths were changed, no worker was started, no notification was sent, no database/file data was modified, and no tests were run locally. The storage inventory remains partial; no global maintenance coordinator should be implemented or advertised as complete yet.