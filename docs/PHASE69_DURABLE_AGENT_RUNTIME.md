# Phase 69 — Durable agent task runtime

Phase 69 introduces a small standard-library-only SQLite task store. It provides durable task state and coordination primitives for future Yuki workers; it does not itself call models, execute tools, or claim production-grade autonomous execution.

## Implemented

- Persistent task records and a structured event ledger.
- Explicit approval by default; callers can opt out for low-risk tasks.
- Idempotency keys for safe task submission retries.
- Bounded retry counts, task deadlines, token/time budget metadata and worker leases.
- Atomic task claims using SQLite write transactions.
- Lease expiry recovery and ownership checks for heartbeats/completion.
- Cancellation, status filtering and persisted JSON results.
- Finite JSON validation and input bounds.
- Dedicated unit tests using temporary databases.

## Example

```python
from pathlib import Path
from yuki_agent_runtime import TaskStore

store = TaskStore(Path.home() / ".yuki" / "agent_tasks.sqlite3")
task = store.submit(
    "repository_review",
    {"repository": "owner/repo", "goal": "inspect test failures"},
    idempotency_key="review-owner-repo-2026-01",
    approval_required=True,
    max_attempts=2,
    budget_seconds=600,
)

# Approval should come from an authenticated, authorized user-facing control.
store.approve(task["id"], approved_by="authenticated-owner")

# A worker claims the job, then separately enforces its tool and network policy.
claimed = store.claim_next("worker-1", lease_seconds=60)
if claimed:
    store.heartbeat(claimed["id"], "worker-1", lease_seconds=60)
    store.complete(claimed["id"], "worker-1", {"accepted": True})
```

## Important safety boundary

- This module is a task queue and state machine, not a worker or sandbox.
- Approval is a data field; the application must authenticate the approver and enforce authorization at the call site.
- Token and time budgets are recorded as limits for a worker to enforce; this module cannot observe actual model token usage or stop external subprocesses.
- Cancellation updates durable state. A running worker must check task status or receive a cancellation signal and terminate its own child processes.
- A lease is a coordination mechanism, not a security boundary. Workers must verify ownership before side effects and use idempotent operations where possible.
- SQLite is suitable for a single-host MVP. Multi-host deployments need a server database/queue and tested migrations.
- Never pass task payloads directly to a shell or treat crawled pages, model output, skills or repository instructions as trusted commands.
- Secrets must be stored in a secret manager or environment, never in task payloads, results or events.

## Production gaps remaining

The next phase should add a worker interface and cancellation polling, structured policy/permission manifests, migration/version management, concurrency stress tests, observability, and integration tests against actual provider adapters. Before production use, validate backup/recovery and database path permissions.
