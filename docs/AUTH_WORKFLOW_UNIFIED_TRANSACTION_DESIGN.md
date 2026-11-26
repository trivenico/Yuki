# Authentication and workflow revocation: unified transaction design

**Status:** Proposed architecture; not implemented  
**Review date:** 2026-11-26  
**Scope:** Private single-owner deployment, desktop/CLI control plane, workflow approval and worker claim.

## Problem statement

Yuki currently stores authentication state in `auth.sqlite3` and workflow state in `tasks.sqlite3`. An approval stores `SHA256(session_token)` in `workflow_runs.approved_session_hash`. The authentication store uses the same SHA-256 token digest as `auth_sessions.token_hash`.

The workflow store has its own `revoked_approval_sessions` table. Desktop controller logout normally writes a workflow revocation marker before revoking the auth session, but that ordering is a caller convention, not an invariant enforced by `BuiltInAuthService.revoke_session()` or `disable_user()`. Any direct auth revocation path can omit the workflow marker.

A live-session callback immediately before claim would still have a check/use race because the auth check and workflow claim commit in separate SQLite databases.

## Preferred design: one local control-plane database

Introduce a single private `control.sqlite3` database containing both existing auth tables and workflow tables. Keep the logical service/repository interfaces separate, but configure `BuiltInAuthService` and `WorkflowRunStore` to use the same canonical database path.

The worker claim transaction must validate, inside the same `BEGIN IMMEDIATE` transaction that changes the workflow row from queued to running:

1. Approval metadata exists and matches the task request digest.
2. Approval has not expired.
3. `approved_session_hash` resolves to an `auth_sessions.token_hash` row.
4. That session has `revoked_at IS NULL` and `expires_at > now`.
5. Its associated `auth_users` row has `disabled = 0`.
6. The session user matches the approval actor recorded for the task (persist an explicit approved user ID if the current schema does not provide an unambiguous binding).

Because both claim and revocation write within the same SQLite database, SQLite serializes their write transactions. If revocation commits first, claim observes the revoked/disabled state and fails. If claim commits first, the task is already running; revocation must separately cancel cooperatively and prevent consequential external side effects through target-side authorization/fencing. Do not claim that a database transaction can retroactively stop an in-flight handler.

The same live-session predicate must guard approval creation (`approve()` and `approve_with_event()`), worker claim, and every direct/evented transition to `running`. Approval creation must verify that the supplied session digest resolves to a currently live session whose user ID equals `actor_id`, within the same `BEGIN IMMEDIATE` transaction that writes approval metadata and its audit event. This prevents an expired/revoked session from creating fresh approval records after a prior out-of-transaction authorization check. The durable local revocation-marker table may remain as an audit/defense-in-depth record, but it is no longer the source of truth for whether a session is live.

### SQLite constraints

- All writers must open the same canonical file path; resolve aliases and reject accidental split paths at composition time.
- Configure foreign keys, busy timeout, synchronous policy, and WAL consistently on every connection.
- Keep transactions short. Never call providers, plugins, filesystem operations, or external APIs while holding the SQLite write lock.
- Session revocation and user disable must commit in the auth service's transaction. Workflow claim performs its session predicate in its own write transaction against the same database file.
- Add indexes for session token hash, expiry, user ID, workflow state, and approval digest as appropriate.
- Avoid cross-database `ATTACH` as the security boundary. WAL mode does not provide atomic multi-database transactions; use one database file.

## Data migration and cutover plan

This is a maintenance-window migration, not a live dual-write migration.

1. **Preflight:** inventory every table, index, trigger, foreign key, schema version, row count, and relevant sequence in both source databases. Confirm both files are healthy and their paths are private. Verify adequate free space and a tested owner backup.
2. **Quiesce:** stop desktop, CLI services, task workers, schedulers, and all writers. Refuse migration if a worker lease is active or a task is in an unresolved external side-effect phase.
3. **Snapshot:** use SQLite's online backup API for each source database after writers are stopped. Preserve original files unchanged. Record snapshot digests, schema versions, row counts, and timestamps in a migration manifest.
4. **Build destination:** create a new `control.sqlite3` from the snapshots. Copy auth and workflow tables while preserving primary keys, session hashes, task IDs, idempotency keys, approval metadata, audit events, revocation markers, and lease state. Do not copy raw session tokens; none should be stored in these tables.
5. **Validate:** run `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, schema checks, row-count checks, unique-key checks, approval/session binding checks, and application-level read-only validation. Confirm every approval hash resolves to the expected auth session and actor. Flag legacy approvals without expiry and fail them closed.
6. **Cut over:** atomically update the private profile's database-path configuration/manifest so both services use the destination. Start no worker until the application verifies the unified path and all startup invariants.
7. **Smoke test:** authenticate, approve a disposable task, claim it, revoke a disposable session, and prove a later claim is denied. Run cross-connection and multi-process race tests before enabling any real specialist worker.
8. **Retain recovery sources:** preserve the two original databases and pre-migration backups read-only for a defined rollback window. Once the new database accepts writes, do not blindly switch back to stale source files; rollback requires a controlled reverse migration or restore that accounts for every post-cutover write.

Migration must be idempotent, collision-safe, restartable before cutover, and must never overwrite source databases. A failed validation leaves the old application configuration active.

## Required implementation sequence

1. Add an explicit control-plane database path abstraction and startup assertion that auth and workflow stores share the same canonical database identity in production composition.
2. Add a workflow-store live-session predicate using the shared connection and transaction. Fail closed if auth tables are missing or malformed; do not fall back to the local revocation-marker-only behavior.
3. Bind approval to the immutable authenticated user ID and session digest. Reject mismatches.
4. Apply the predicate to claim and every transition into running, including evented APIs.
5. Make all session revocation and account-disable operations update auth state transactionally. Record an audit event in the same control database transaction where feasible.
6. Add schema compatibility tests for fresh databases and existing databases with the additive approval-expiry migration.
7. Implement the offline two-database migration planner/executor with dry-run, backups, integrity checks, collision handling, restart recovery, and explicit owner confirmation.
8. Update desktop and CLI composition to use the unified database. Ensure no hidden second database path remains in any worker or coordinator.
9. Add multi-process race tests using independent SQLite connections and processes:
   - revoke commits before claim: claim denied;
   - claim commits before revoke: claim is running and subsequent target authorization denies new effects;
   - disable user before claim: claim denied;
   - session expiry before claim: claim denied;
   - auth schema missing/corrupt: claim denied;
   - two workers racing one approved task: exactly one claim;
   - restart after migration/cutover: revocation remains enforced.
10. Keep consequential specialist execution disabled until CI passes on Linux, Windows and macOS where supported, migration recovery is tested, and target-side effect fencing is independently verified.

## Rejected shortcuts

- A callback that calls `resolve_session()` and then separately calls `claim_next()`: still has a cross-database time-of-check/time-of-use race.
- Treating the workflow revocation marker as authoritative: auth revocation paths can bypass it.
- A best-effort outbox projection without a strict freshness/availability contract: an unprojected revocation can still be consumed.
- Assuming task claim or desktop logout stops a running Python handler: in-process work and external effects need separate cancellation and fencing.
- Switching database paths without migrating and validating both databases: risks losing users, sessions, tasks, audit history, and retry idempotency.

## Current readiness

The existing ten-minute approval expiry and workflow-local revocation marker remain defense-in-depth only. Live auth validity at claim is not implemented. The unified database design is a proposal; no schema, runtime, data, or deployment changes are made by this document.

## Read-only migration preflight prototype

The branch now contains `core/control_plane_migration.py` and `tests/test_control_plane_migration.py`.

The planner accepts the legacy auth database, workflow database, and proposed destination path. It checks that the three paths are distinct; inspects lexical parent components without following symlinks; rejects non-regular or hard-linked database files; opens source databases read-only; runs SQLite `quick_check`; inventories application table names and row counts; checks required auth/workflow tables and columns; records `user_version`, journal mode, file metadata, and SQLite sidecar metadata; and refuses an existing destination.

The planner is intentionally **metadata-only**. It does not read account, password-hash, session, task-definition, result, or event row values. It never creates a destination, invokes SQLite backup, copies rows, changes permissions, or alters either source. Even a clean result returns `executable=False`; a separate reviewed executor and explicit owner approval are still required.

The prototype checks required baseline tables/columns, table row counts, SQLite quick-check, per-source foreign-key integrity, schema object names/types, a schema-definition fingerprint, and selected workflow quiescence hazards. It still does not prove compatibility of the combined schema, preserve or validate all object definitions in a merged destination, establish a quiescence lock, detect all external writers, verify Windows ACLs, or produce a recoverable merged database. Its path inspection is advisory and cannot eliminate filesystem time-of-check/time-of-use races; it is not a sandbox or migration authorization. Those remain executor/cutover gates. It is not wired into desktop startup or a CLI command.

The existing `ui/desktop/migration.py` migrates a closed directory tree and backs up each SQLite file separately. It is not a substitute for merging two independent databases into one transactional control plane.


### Preflight coverage extension

The preflight now also inventories SQLite schema object types/names (tables, indexes, triggers, and views), executes `PRAGMA foreign_key_check`, and reports nonterminal workflow count and running rows with a lease owner/expiry. A foreign-key violation blocks the source as corrupt. These are metadata/counts only; no task or account row values are returned.

A nonzero nonterminal count is a **quiescence warning**, not a corruption finding. Before any executor runs, the owner must stop every writer/worker and explicitly reconcile queued, running, waiting-approval, and interrupted work. The preflight cannot prove that no external process is writing; SQLite's read-only inventory is not a global application maintenance lock. The future executor must acquire an application-level maintenance fence and recheck source identities/state immediately before backup.


### Schema fingerprint and workflow-state safety checks

The read-only inventory now hashes the ordered SQLite schema definitions (object type, name, and SQL text) into a SHA-256 fingerprint. This provides a stable schema-change signal for a later reviewed migration manifest; it is not a file-integrity signature or a trust anchor.

It also distinguishes:
- running rows with complete lease metadata;
- malformed running rows missing either lease owner or expiry;
- approval records carrying a session hash but no approval expiry.

Malformed running leases and legacy approvals without expiry are surfaced as migration blockers requiring explicit reconciliation/fail-closed handling. A count of zero complete leases alone must never be interpreted as proof that the workflow store is quiescent.


### Maintenance-fence requirements before executor work

A lock file created only by the migration command is insufficient: existing app processes do not currently coordinate through it. A valid maintenance fence must be enforced by every writer path, including auth account/session mutations, workflow creation/approval/claim/transition/event writes, CLI commands, desktop background workers, recovery jobs, and any standalone worker process.

The implementation must:
1. Define one canonical private control-plane lock/maintenance authority and fail closed if it is unavailable or malformed.
2. Require every mutating service operation to acquire a shared-operation lease; migration obtains an exclusive lease. The lock must be cross-process and cross-platform, with owner/process metadata for diagnostics but never stale-lock auto-break based only on PID absence.
3. Ensure the lock covers the complete transaction, not just an initial check. A writer must not pass a maintenance check and then commit after migration has acquired exclusivity.
4. Stop task dispatch and wait for or explicitly reconcile in-flight handlers and external effects. An empty SQLite lease count does not prove no worker process is executing.
5. Under exclusive maintenance, checkpoint/backup each source with SQLite's backup API, capture source file identity and schema fingerprints, then recheck them before cutover.
6. Keep originals immutable and retained until the merged database passes schema, row-count, primary/foreign-key, uniqueness, sequence, and application-level invariants. Never overwrite an existing destination.
7. Define crash recovery for each stage. Once the new database accepts writes, restoring an old source snapshot is not a safe rollback; recovery must use a verified forward repair or an explicitly fenced restore procedure.
