# PostgreSQL production migration operations

Status: **release blocker / operational design**. This document records the current repository boundary and the controls required before PostgreSQL-backed Yuki services are enabled. It is not an executable migration runner and does not authorize applying SQL to any database.

## Verified current state

- SQL migrations are versioned under `migrations/security/`, currently through `0015_agent_plan_dispatch_cancellation.sql` in the agent-plan branch under review.
- PostgreSQL integration tests apply selected migration files directly with psycopg. For example, `tests/test_postgres_agent_plan_repository.py` applies workflow/ownership migrations and agent-plan migrations `0012`–`0015` explicitly.
- PostgreSQL repositories perform schema/readiness checks for their own required tables, columns, constraints, and indexes.
- `docs/PRODUCTION_SECURITY_BOOTSTRAP.md` says production web composition is deployment-owned and that the stock `python -m scripts.run_web` entry point deliberately fails closed without an injected production runtime.
- The reviewed application tree does not establish a single production migration command, migration ledger, global ordering/locking mechanism, or deployment hook that proves every required migration is applied before traffic is admitted.

Therefore, test setup is not evidence of production migration execution. Do not assume migrations are applied automatically at startup, and do not enable a PostgreSQL-backed service based only on repository-local schema checks.

## Required migration-runner contract

Before production rollout, implement or designate one deployment-owned migration executor with all of these properties:

1. **Single ordered source of truth.** Discover only reviewed, explicitly registered migration files. Sort by immutable numeric/version identifier; reject duplicate, missing, or out-of-order versions.
2. **Durable ledger.** Record version, filename, SHA-256 checksum, applied timestamp, executor identity, and execution outcome in a protected schema. Never silently re-run a changed checksum for an already-applied version.
3. **Concurrency control.** Acquire a PostgreSQL advisory lock for the migration namespace before reading/updating the ledger. A second deployer must wait or fail safely.
4. **Transactional semantics.** Execute each migration using the transaction behavior it explicitly declares. Reject transaction-incompatible statements in transactional migrations. Do not pretend a partially applied non-transactional migration is atomic.
5. **Preflight.** Verify database identity, PostgreSQL version, required privileges, connectivity, and backup/restore readiness before mutation. Refuse unknown or unsupported schema states.
6. **Failure handling.** Stop on first failure, preserve diagnostic details in deployment logs without leaking secrets, leave the service unready, and require a reviewed repair/reconciliation procedure. Never advance the ledger on partial failure.
7. **Deployment ordering.** Run migrations as a dedicated release job before application rollout, not concurrently in every web/worker process. Application startup must validate schema compatibility and fail closed when required versions are missing.
8. **Expand/contract changes.** Prefer backward-compatible expand, deploy, backfill, and contract phases. Destructive changes require a separate approved release after old application versions are gone.
9. **Backup and restore.** Take a recoverable backup or verified snapshot before high-risk changes. Test restore against the exact migration sequence; define RPO/RTO and rollback/forward-fix procedures.
10. **Observability.** Emit migration version, duration, outcome, and schema readiness metrics. Alert on failed/long-running migrations and application/schema version mismatch.

## Agent-plan migration gate

The agent-plan repository currently expects migrations:

- `0007_workflow_runs.sql`
- `0009_external_operations.sql`
- `0010_workflow_task_ownership.sql`
- `0012_agent_plan_repository.sql`
- `0013_agent_plan_outbox_claim_fencing.sql`
- `0014_agent_plan_subtask_action.sql`
- `0015_agent_plan_dispatch_cancellation.sql`

This is the integration-test dependency set, not a claim that these are the complete production migration set. Other PostgreSQL subsystems have their own dependencies. The release migration manifest must be derived from the complete deployed schema and all active repositories, not copied from one test fixture.

Before agent-plan PostgreSQL cutover:

- prove all prerequisite migrations are present in the production manifest;
- verify the executor applies them in the supported order and records checksums;
- run repository schema readiness against a newly provisioned database and an upgraded representative database;
- run the complete PostgreSQL concurrency/fencing suite;
- run coordinator-level submit, approval, dispatch, cancellation, crash-recovery, and ownership tests;
- verify rollback/forward-fix and restore procedures;
- keep SQLite as the active coordinator backend until every gate is green.

## Operational rollout sequence

1. Inventory every production PostgreSQL repository and its migration dependencies.
2. Choose the deployment owner and execution environment for migrations.
3. Implement the migration ledger, locking, checksums, preflight, and failure semantics.
4. Add CI tests for fresh install, upgrade from supported prior versions, duplicate execution, checksum drift, concurrent deployers, and injected mid-migration failure.
5. Exercise backup/restore and forward-fix drills on a disposable production-like database.
6. Run the migration job during a controlled release; inspect its recorded result.
7. Start application instances only after schema compatibility and all production security readiness checks succeed.
8. Monitor readiness and migration telemetry through the rollout.

## Explicit non-goals

- Do not run schema migrations automatically from every ASGI startup or worker boot.
- Do not execute arbitrary SQL discovered from an untrusted directory.
- Do not use a test database URL or test-only schema bootstrap in production.
- Do not treat a successful repository constructor as proof that the entire application schema is current.
- Do not roll back by deleting migration ledger rows or manually editing applied SQL history.
- Do not switch the agent-plan coordinator to PostgreSQL until the repository-port, coordinator integration, and migration gates are complete.

## Validation status

This is an architecture/operations gate based on source review. No migration runner was found in the reviewed application tree; no production database was contacted, changed, or migrated. The deployment owner must verify the actual hosting pipeline before rollout.
