# Runtime Integration Contract (Phase 28)

## Purpose

Define the security and lifecycle contract that must hold when restoring the approval and skill execution modules. This document is a review gate, not a claim that the end-to-end runtime is already integrated.

## Required execution sequence

1. Validate the manifest and normalize arguments against a declared schema.
2. Create or load a durable task record with a caller-provided idempotency key.
3. Apply the permission policy to the exact action and normalized argument snapshot.
4. Record the approval decision successfully before dispatching side effects.
5. Transition the task to `running` with an optimistic version check.
6. Execute in a bounded subprocess/sandbox with an explicit environment, working directory, timeout, output limits, and process-tree cleanup.
7. Persist a bounded result or a sanitized error code.
8. Record the terminal audit event. If execution may have happened but result/audit persistence fails, transition to `recovery_required`; never automatically replay an uncertain side effect.

## Fail-closed invariants

- Missing approver, malformed risk, invalid manifest, denied permission, or audit write failure must prevent new execution.
- Approval must be bound to the action and normalized arguments being executed; changing either invalidates the approval.
- Approval tokens must not be written to logs or audit payloads in plaintext.
- A state transition must use the expected version and an explicit allowed transition.
- A task found in `running` after restart must be reconciled, not blindly retried.
- External side effects cannot be guaranteed exactly-once by SQLite alone. Use downstream idempotency keys or query the downstream operation before resuming.
- Subprocess failure, timeout, cancellation, and cleanup failure must be distinguishable in the task record without storing secrets or unbounded output.

## Required integration tests

- Invalid manifest and invalid arguments never reach the executor.
- Missing/denied approval never reaches the executor.
- Approval for a different action or argument snapshot is rejected.
- Audit failure before execution blocks dispatch.
- Executor timeout kills the process tree and records a bounded failure.
- Result persistence failure after a possible side effect produces `recovery_required`.
- Audit failure after execution does not trigger automatic replay.
- Restart recovery identifies stale `running` tasks and does not re-execute them.
- Idempotency-key reuse with a different payload is rejected.
- Tests cover Windows, macOS, and Linux.

## Upstream reuse gate

For every imported component, record repository URL, exact commit, license/SPDX identifier, copied or adapted paths, local modifications, dependency additions, security assumptions, and tests. Do not copy a whole framework into the runtime before its interfaces and dependency tree have been reviewed.

## Exit criteria

This phase is complete only when the contract is enforced by the runtime (not just documented), integration tests pass on all supported CI platforms, and the security-sensitive execution path is reviewed. A green unit-test suite alone is not proof of production readiness.
