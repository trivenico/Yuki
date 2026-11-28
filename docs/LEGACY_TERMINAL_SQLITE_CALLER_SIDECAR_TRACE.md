# Legacy terminal SQLite caller and sidecar trace

Date: 2026-11-28

## Scope and evidence

This trace follows the source-relative SQLite paths declared in the root
`yuki.py` compatibility terminal and the direct constructors for
`DurableTaskEngine` and `SkillApprovalStore`. It is a repository source
trace, not evidence about external scripts, installed copies, or runtime use.
GitHub code search on the default branch returned no matches for these exact
identifiers; because the active work is on a feature branch and code search is
index-bounded, that negative result is not proof that no other caller exists.

## Callers and lifetime

- `_get_durable_task_engine()` creates the parent directory and constructs
  `DurableTaskEngine(_DURABLE_TASK_DB)`.
- The legacy dispatcher calls it for `/durable goal create`, task submit,
  list, cancel, and `/durable run`. Each command uses the engine as a context
  manager, so its owned in-memory anchor (if any) is closed on exit.
- `/durable run` calls `recover_expired_leases()` and then
  `DurableSkillWorker.run_once()`. The worker claims one task and invokes the
  supplied skill runtime in-process. This is an executable legacy command,
  not merely a data-management interface.
- `_get_skill_approval_store()` likewise creates the parent and constructs
  `SkillApprovalStore(_SKILL_APPROVAL_DB)`. The legacy
  `_build_approved_skill_runtime()` calls it before dispatching each
  `/skill` command, including listing/inspection paths. The store's
  persistent manifest approval is distinct from
  `ApprovedSkillRuntime.approve_invocation()`, which grants an exact
  process-local one-shot invocation. Neither is an authenticated owner/session
  boundary.
- The store constructor creates its schema and each operation opens SQLite
  directly. Neither constructor validates the canonical private profile,
  rejects symlinked path components, or injects the canonical
  `MaintenanceFence`. The legacy parent `.yuki/` is source-relative and
  may be in a shared checkout.

## Files and sidecars

The declared main database paths are:

- `.yuki/durable_tasks.sqlite3`
- `.yuki/skill_approvals.sqlite3`

The code does not explicitly manage SQLite journal, WAL, or shared-memory
sidecars. SQLite may create transient journal files during writes; WAL/SHM
files can exist if journal mode has been changed or a prior process left them.
A future inventory/backup must enumerate each database and its sidecars while
the host is quiesced and use SQLite's backup mechanism; copying only the main
file while a writer is active is not a consistent backup. This audit did not
inspect any on-disk database or filesystem.

## Decision

No narrow runtime guard was added. Both stores are opened from shared
command-dispatch helpers, and a guard that only blocks `/durable run` would
leave other SQLite writes and the skill invocation path outside the profile
and maintenance boundary. A path-only guard would also not establish
authenticated approval or safe worker lifecycle. Fail-closed disabling of
legacy commands could break existing owner workflows without resolving the
other writers.

Keep the legacy execution path experimental and unavailable for consequential
or unattended work. Any compatibility change should be a coordinated explicit
migration: inventory the main DBs and sidecars, quiesce every writer, take
SQLite-consistent backups, establish owner-private destination and shared
fence, add authenticated owner/session authorization and worker drain, verify
rollback, and preserve source files until validation. No paths, schemas,
permissions, database bytes, or runtime behavior changed in this trace.

## Validation

Source inspection only. No database was opened, created, migrated, or
modified; no worker or skill was invoked; no tests were run locally. This trace
does not prove absence of external callers or application-wide quiescence.
