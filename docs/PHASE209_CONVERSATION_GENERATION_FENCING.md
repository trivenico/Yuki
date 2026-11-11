# Phase 209 — conversation generation persistence fencing

## Scope

Bind conversation-generation checkpoints, terminal status transitions, and
assistant-message persistence to the lease token held by the current service
execution. A token from a previous lease holder must not mutate these records
after its lease has been released and another worker has acquired the slot.

## Implemented contract

- The service passes its unguessable lease token through ordinary generation
  persistence operations.
- Checkpoint and terminal-state SQL updates require a matching lease row and
  token. A missing token is accepted only when no lease row exists, preserving
  explicit recovery/test operations that run outside an active lease.
- Assistant messages using the generation idempotency namespace are rejected
  when a lease exists but the supplied token does not match.
- Recovery performed inside the service uses the currently held lease token.
- A stale token is rejected even if the old worker resumes after a new lease is
  acquired. The database check is performed in the same statement/transaction as
  the mutation.

## Required validation

- Current holder can checkpoint, append its assistant result, and finish.
- After release and reacquisition, old holder cannot checkpoint, finish, or
  append an assistant result.
- Existing interrupted-generation reconciliation remains functional.
- Run complete cross-platform quality gates and SQL translation tests.

## Critical remaining gaps

This is **not complete end-to-end worker fencing**. It fences conversation
persistence only. It does not yet fence:
- model-provider requests already in flight;
- guarded tool execution or external side effects;
- approval consumption, credential broker operations, or task/worker state;
- writes to other databases, memory indexes, logs, or external systems.

A stale worker may therefore still perform an external action unless that
action's own service validates a revocable worker-bound execution credential
and fencing generation immediately before committing the side effect.

Lease release is still not supervised crash recovery. Expiry does not prove a
worker stopped. No operator recovery API or CLI, worker termination proof,
credential revocation workflow, immutable recovery audit ledger, or cross-store
deletion coordinator is delivered here.

## Readiness

This is a narrowly scoped persistence-safety improvement. It is not a production
readiness declaration for conversation deletion, multi-host worker recovery, or
agent execution. PostgreSQL live-service integration and end-to-end tests remain
required; CI must be checked against the exact PR head.
