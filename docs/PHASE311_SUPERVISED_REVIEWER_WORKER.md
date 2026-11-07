# Phase 311 — Supervised reviewer worker

## Scope

Adds a bounded, synchronous worker loop over the existing SQLite reviewer
dispatch outbox. It is a reusable worker service, not an always-running daemon
or a claim of operating-system/container isolation.

## Flow

1. Claim only dispatches assigned to the worker identity.
2. Reconstruct and validate the review task from the outbox payload.
3. Resolve session and lease credentials through a trusted host-side callback.
4. Confirm the provisioned credentials bind to the exact task and worker.
5. Read the authoritative task version from the ledger.
6. Execute through `ReviewerExecutionAdapter`.
7. Re-read the ledger and acknowledge the dispatch only after a submitted
   result is reported as signature-verified.

## Failure semantics

- Credential/task provisioning failures occur before the ledger task is claimed;
  these may be returned to the outbox retry policy.
- Once the task may have transitioned to `in_progress`, the worker does not
  blindly retry or acknowledge it. The dispatch claim is allowed to expire and
  the existing reviewer lease supervisor remains responsible for recovery.
- A worker finding an already submitted and integrity-verified task can safely
  acknowledge its dispatch.
- The worker never reads credentials from outbox records and never receives
  signing keys or database handles in the reviewer executor callback.

## Restart and deployment boundaries

The worker loop is restartable because dispatch state is durable, but session
and lease credentials are intentionally not stored in the outbox. A host
credential resolver must retrieve current credentials from protected storage.
A worker process must be started and supervised by an operator or a future
service manager; this phase does not add a daemon, OS service, scheduler,
distributed queue, heartbeat, or automatic credential reconstruction.

## Verification

Run:

```bash
python -m unittest tests.test_reviewer_worker -v
```

These tests exercise orchestration with mocks. They do not launch Docker or
prove real provider inference, host credential-vault behavior, or multi-process
production reliability.
