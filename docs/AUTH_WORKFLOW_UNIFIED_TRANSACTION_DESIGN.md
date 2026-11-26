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