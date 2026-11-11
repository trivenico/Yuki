# Phase 207 — transactional companion state

**Status:** Experimental persistence foundation. Not an encrypted-at-rest or production desktop release.

## Implemented

- Replace separate JSON read/modify/write stores with one owner-scoped SQLite database at `companion/state.sqlite3`.
- Persist memory, consented identity labels and reminders in records keyed by owner, record type and record ID.
- Use SQLite transactions and `BEGIN IMMEDIATE` for reminder claims and notification outbox leases, so concurrent companion processes cannot lease the same event.
- Preserve existing public facade methods for memory, identities and reminders.
- Keep memory disabled by default. The runtime does not import the legacy memory file unless memory is explicitly enabled.
- Import legacy JSON arrays once, transactionally, while preserving source files for rollback and recovery.
- Reject malformed legacy records rather than overwriting or silently discarding them.
- Apply restrictive POSIX database file permissions and reject a symlink database path.
- Enforce per-owner record limits and atomically reject duplicate identity labels.

## Security and operational boundaries

- SQLite state is **not encrypted at rest**. File permissions are not encryption; OS credential integration and encrypted backup/restore remain future work.
- Workspace access control remains the local trust boundary. The owner ID is a scope identifier, not an authentication credential.
- No scheduler, OS notification delivery, background daemon, microphone activation, device-control transport or calling feature is added.
- The version-2 notification outbox separates due-event enqueue, delivery lease, successful completion and retry. Lease tokens are random, only their SHA-256 hashes are stored, and stale/expired lease tokens are fenced. Retries have bounded delay and attempt limits; terminal failures remain inspectable. A stable event ID is exposed as the provider idempotency key.
- External delivery is **at-least-once**, not exactly-once: a crash after a provider accepts a notification but before `complete_outbox` commits can cause a retry. Duplicate suppression depends on the provider honoring the stable idempotency key. No provider adapter or OS notification sender is wired yet.
- Legacy JSON files are intentionally retained. Removal requires a separate verified backup/restore and migration-retention policy.
- The schema is versioned and upgrades version 1 to version 2 by creating the outbox table and index transactionally. Future schema versions still require explicit migrations and rollback tests.
- The local database and its WAL/SHM files need a protected workspace and backup strategy.

## Validation added

- Owner isolation across two store instances.
- One-time, non-destructive legacy import and malformed-file preservation.
- Concurrent reminder-claim deduplication across separate store instances.
- Version-1 to version-2 schema migration.
- Idempotent outbox enqueue, concurrent lease exclusivity, expired-lease recovery, stale-token fencing, retry scheduling, terminal failure and owner isolation.
- Symlink rejection and POSIX file mode check.
- Rejection of timezone-naive reminder claim times.

CI must pass on the exact PR head. No local test execution, encrypted storage claim, independent security review, desktop deployment or real notification delivery is claimed.
