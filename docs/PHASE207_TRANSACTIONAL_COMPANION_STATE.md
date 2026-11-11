# Phase 207 — transactional companion state

**Status:** Experimental persistence foundation. Not an encrypted-at-rest or production desktop release.

## Implemented

- Replace separate JSON read/modify/write stores with one owner-scoped SQLite database at `companion/state.sqlite3`.
- Persist memory, consented identity labels and reminders in records keyed by owner, record type and record ID.
- Use SQLite transactions and `BEGIN IMMEDIATE` for reminder claims, so two concurrent companion processes cannot both claim the same due reminder.
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
- Reminder claims are at-most-once at the database boundary. A crash after claim but before a notification is delivered can lose that notification; a durable delivery/outbox state machine is required for reliable notification delivery.
- Legacy JSON files are intentionally retained. Removal requires a separate verified backup/restore and migration-retention policy.
- The schema is versioned, but forward migrations beyond version 1 have not yet been implemented.
- The local database and its WAL/SHM files need a protected workspace and backup strategy.

## Validation added

- Owner isolation across two store instances.
- One-time, non-destructive legacy import and malformed-file preservation.
- Concurrent reminder-claim deduplication across separate store instances.
- Symlink rejection and POSIX file mode check.
- Rejection of timezone-naive reminder claim times.

CI must pass on the exact PR head. No local test execution, encrypted storage claim, independent security review, desktop deployment or real notification delivery is claimed.
