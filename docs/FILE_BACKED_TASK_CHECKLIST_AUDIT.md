# File-backed yuki_tasks checklist audit

**Review date:** 2026-11-28  
**Status:** manual checklist only; not an agent execution queue.  
**Scope:** `yuki_tasks.py`, its legacy `yuki.py` terminal caller, shared JSON locking/writing primitives, and focused regression tests.

## Actual call path and semantics

- `yuki.py` imports `load_tasks`, `add_task`, `list_tasks`, `complete_task`, and `delete_task` from `yuki_tasks.py`.
- At startup, the legacy terminal prototype loads the checklist once. The `/task add`, `/task done`, and `/task delete` commands mutate it; `/task list` only renders descriptions and completion flags.
- The checklist has no handler registry, worker, scheduler, task claim, retry, approval, tool dispatch, shell execution, or autonomous side-effect path. It must not be conflated with `DurableTaskEngine`, `TaskStore`, or the authenticated `WorkflowRunStore`.
- The installed `yuki` CLI in `yuki_cli.py` is a separate conversation-only entry point and does not expose this checklist command set.

## Persistence controls

- The store is one JSON list at `yuki_tasks.py`'s adjacent `yuki_tasks.json`; it is not under the canonical `YUKI_HOME`/private-profile data root.
- Reads and read-modify-write mutations use `core.json_file_store.file_lock()`, which combines a process-local reentrant lock with an OS file lock. The add/complete/delete operations compare the caller's in-memory snapshot with the latest file under that lock, preventing stale snapshot writes.
- Atomic writes create a temporary file, fsync it, replace the target, fsync the parent directory on POSIX, and use mode 0600 on POSIX. The lock file is also created owner-only on POSIX.
- Important privacy limitation: `ensure_private_directory()` leaves existing directories unchanged. The source checkout/repository directory is therefore not made owner-private by this store. The JSON and lock files are owner-only on POSIX, but directory-level privacy and non-POSIX ACLs are not established by this module. Do not store secrets or sensitive personal data in checklist descriptions.
- The legacy `save_tasks()` helper previously replaced the entire list under a lock but did not compare a caller snapshot, allowing a stale writer to overwrite newer data. It now requires `expected_snapshot` and performs compare-and-swap under the lock. No in-repository caller of `save_tasks()` was found.
- The loader previously filtered malformed entries out of a valid top-level list. A later mutation could then persist the filtered list and silently destroy malformed records. It now fails closed on malformed entries and duplicate/non-positive IDs; no mutation occurs after a failed load.
- No schema version, migration mechanism, retention limit, or recovery/backup workflow is defined for this simple checklist.

## Changes and tests

- Hardened `_load_tasks_unlocked()` to reject malformed entries and duplicate/non-positive IDs rather than silently dropping them.
- Hardened `save_tasks()` with a required expected snapshot and task-ID validation.
- Added `tests/test_yuki_tasks.py` for basic CRUD, stale snapshot rejection, compare-and-swap save, malformed-file preservation, duplicate IDs, and invalid replacement rejection.
- Tests were authored but not run locally.

## Decision

Keep this module as a low-risk manual checklist only. Do not attach model-directed execution or handlers to it. If it remains a supported legacy interface, a later migration should move its data to the canonical private profile using an explicit backup/restore step and a compatibility plan; do not silently change the file path while existing checklists may contain user data. No migration was performed, no worker or task handler was started, and no deployment occurred.
