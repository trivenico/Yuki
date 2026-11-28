# Legacy terminal JSON persistence boundary audit

Date: 2026-11-28

## Scope

Inspected the root `yuki.py` compatibility terminal and its JSON-backed
memory, task-list, learning-history, and audit-event helpers, plus
`core/json_file_store.py` and `core/portable_paths.py`. This is a source
review only; it does not inspect the host filesystem or prove which copies of
the legacy terminal are in use.

## Stores and callers

The four source-relative files are:

- `yuki_memory.json` — loaded at terminal startup and changed by memory
  commands and agent outcomes.
- `yuki_tasks.json` — loaded at startup and changed by task-list commands.
- `yuki_learning.json` — read by learning/dashboard helpers and changed by
  outcome recording or history clearing.
- `yuki_audit.json` — read by audit/dashboard helpers and appended by
  provider/skill command paths.

These are distinct from the canonical private profile and from the separate
`.yuki/durable_tasks.sqlite3` and `.yuki/skill_approvals.sqlite3` stores.
The source-relative defaults preserve compatibility and can reside in a shared
checkout.

## Existing protections and limits

- `file_lock()` serializes cooperating operations for one file using a
  sibling lock file and process-local lock. It checks the lock file is regular
  and single-link, and uses no-follow opening on POSIX.
- JSON writes use a temporary file, fsync, and atomic replacement. On POSIX,
  the replacement file is mode 0600.
- The helper's own module documentation explicitly assumes a private parent.
  `ensure_private_directory()` creates missing components with restrictive
  modes but deliberately leaves existing directories unchanged. Therefore it
  does not establish that an existing source checkout or ancestor is private.
- Each JSON file has its own lock; none is the canonical cross-process
  `MaintenanceFence`. A backup that holds only the profile fence is not
  coordinated with these legacy writes.
- Reads follow the target path, while atomic replacement replaces the directory
  entry. This review did not establish a symlink policy for the legacy JSON
  targets or their parent chain.
- These protections address cooperating file updates and partial writes; they
  do not provide encryption, authenticated owner/session authorization, or
  global quiescence.

## Decision

Do not silently relocate these files, chmod an existing checkout, or add a
partial runtime guard. A safe transition needs an explicit inventory of all
four files and relevant lock/temp files, quiescence of every legacy writer,
backup and integrity verification, a canonical private destination, and a
rollback plan that preserves the old files until the new profile is verified.
Any future backup guarantee must either fence these writers or explicitly
exclude their files and state that exclusion.

No data files were opened or modified. No paths, permissions, schemas, or
runtime behavior changed.

## Validation

Source inspection only. No local tests were run. External scripts and other
copies of the legacy terminal were not inspected; no claim of application-wide
quiescence is made.
