# Persistent job queue (Phase 13)

`core/job_queue.py` provides a SQLite-backed queue for a future bounded Yuki worker pool.

## Contract
- Durable jobs with JSON-object payloads capped at 256 KB.
- Atomic claims using `BEGIN IMMEDIATE` and worker leases.
- Bounded retries (1–10 attempts), lease-expiry recovery, cancellation, and terminal results.
- A worker may finish only a running job assigned to its worker ID.
- This component does not start workers, execute tools, or grant permissions.
- The orchestrator must re-check authorization and approval immediately before consequential effects.

## Example
```python
from core.job_queue import SQLiteJobQueue
queue = SQLiteJobQueue("data/yuki_jobs.sqlite3")
job = queue.enqueue("research", {"goal": "Compare two business ideas"})
claimed = queue.claim_next("worker-1", lease_seconds=120)
if claimed is not None:
    # Call an approved task handler; never run arbitrary payload code.
    queue.complete(claimed.id, "worker-1", {"status": "draft_ready"})
```

## Operational notes
- Use persistent local disk, not ephemeral container storage.
- Back up SQLite consistently and restrict filesystem permissions.
- Payloads/results may contain private data; define retention, export and deletion before production.
- Multiple processes can claim atomically; this is not a distributed queue or a substitute for monitoring/integration tests.
