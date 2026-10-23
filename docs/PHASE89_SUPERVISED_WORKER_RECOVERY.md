# Phase 89 — supervised worker cycles and lease recovery

## Scope

This phase adds a finite, explicitly invoked worker-supervision cycle. Each cycle first marks expired running leases as `interrupted`, records a durable `task.interrupted` event, and then processes at most a configured number of queued specialist tasks.

## Recovery guarantees

- Recovery requires the internal service principal to have `task:recover`.
- Expired leases are marked interrupted and journaled with `automatic_replay: false`.
- The supervisor never replays an interrupted task and does not automatically retry failed work.
- A new retry/requeue must be an explicit, separately authorized operation with a fresh review of approval requirements.
- Batch size and recovery limit are bounded to 100.
- The supervisor does not create a background thread, scheduler, or daemon. Invoke `run_cycle()` from a managed process, cron, or orchestration platform with explicit lifecycle and health monitoring.

## Operational integration

Use one configured `TaskRuntime` instance for the worker and supervisor. The service identity must be resolved from trusted deployment configuration, not user input, and the runtime authorization callback must explicitly grant `task:recover` only to the service actor. The durable task event journal is the source of truth for interrupted work.

## Validation

Tests cover authorized recovery, durable interruption events, expired lease non-replay, bounded batch execution, idle cycles, and configuration validation. Merge only after exact-head CI and cross-platform repository quality gates pass.
