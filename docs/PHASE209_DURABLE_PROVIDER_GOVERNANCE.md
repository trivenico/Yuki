# Phase 209 — durable provider governance

- Added SQLiteProviderGovernanceStore for atomic provider circuit state, global/provider/agent concurrency reservations, per-agent fixed-window request quotas, expiring leases and metadata-only audit events.
- Each mutation uses SQLite BEGIN IMMEDIATE, WAL mode, busy timeout and FULL synchronous durability.
- Multiple store instances sharing one SQLite database observe the same reservations and circuit state.
- Expired reservations are reclaimed during admission and snapshots; expiry is audited and clears abandoned half-open probe reservations.
- Circuit outcomes are tied to lease IDs. A stale successful request cannot close an open circuit; only the designated half-open probe can close it.
- Audit events reject updates and deletes at the database level. Request payloads, prompts and credentials are never accepted or persisted.

## Deployment limits

- This supports multiple processes sharing one SQLite database on a filesystem with reliable SQLite locking; do not place SQLite WAL databases on arbitrary network filesystems.
- It is not a multi-host network database or a distributed consensus system. For multiple hosts, use a transactional shared database and equivalent constraints.
- Expired leases are recovered on the next store operation, not by a background sweeper. Choose lease timeouts above the enforced provider execution deadline.
- Provider dispatch integration remains a separate rollout step; this store is not automatically wired into all agent paths.
- Wall-clock time is persisted for cross-process expiry. Production hosts must have synchronized clocks; monotonic clocks cannot be persisted across processes/restarts.
