# Phase 150 — Generation leases

Conversation generation uses database-backed leases so independent application workers cannot start duplicate turns for the same conversation. Leases expire after ten minutes and are released with an unguessable token. This remains SQLite-oriented and requires operational validation before multi-host deployment.

## Implementation details

- Lease claims are committed with `BEGIN IMMEDIATE` and a unique row per conversation, so independent processes sharing the same SQLite database serialize claims.
- A random lease token fences release: an old worker cannot delete a replacement worker's lease.
- A lease expires after 3,600 seconds, matching the agent loop's maximum configured duration. Provider adapters must still enforce network timeouts; a hung provider call must not outlive the lease indefinitely.
- Lease acquisition checks authenticated owner scope and rejects archived or unknown conversations.
- This supports multiple processes sharing one SQLite database file on a single host. SQLite WAL is not a multi-host shared database architecture; use a transactional PostgreSQL lease implementation before multi-host deployment.
- Crash recovery is lease expiry, not automatic turn resumption. A user turn persisted before a process crash may require explicit reconciliation; durable generation checkpoints are a separate follow-up.
