# WI-6U9 — Multi-instance security release gate

## Status

Release-gate specification. This document is not evidence that CI, migration rehearsals, backup restoration, failover, or production rollout have succeeded.

The WI-6U PostgreSQL security stack is still under review. The dependent pull requests are open and must remain unmerged until their own required checks and the complete dependency chain have been reviewed. No production multi-replica approval is implied by the existence of implementation code or tests.

## Dependency-ordered change set

The current stacked implementation sequence is:

| Order | Pull request | Scope | Current review state |
|---:|---|---|---|
| 1 | [#1232](https://github.com/trivenico/Yuki/pull/1232) | Multi-instance PostgreSQL security architecture | Open |
| 2 | [#1233](https://github.com/trivenico/Yuki/pull/1233) | Shared leases, ingestion cursors, alert outbox | Open |
| 3 | [#1234](https://github.com/trivenico/Yuki/pull/1234) | Workload-key identity validation | Open |
| 4 | [#1235](https://github.com/trivenico/Yuki/pull/1235) | PostgreSQL workload-key registry | Open |
| 5 | [#1236](https://github.com/trivenico/Yuki/pull/1236) | Registry admin control-plane compatibility | Open |
| 6 | [#1237](https://github.com/trivenico/Yuki/pull/1237) | Fail-closed registry factory | Open |
| 7 | [#1238](https://github.com/trivenico/Yuki/pull/1238) | Shared PostgreSQL security monitor | Open |
| 8 | [#1239](https://github.com/trivenico/Yuki/pull/1239) | Shared authorization-audit contract | Open |
| 9 | [#1240](https://github.com/trivenico/Yuki/pull/1240) | PostgreSQL authorization-audit repository | Open |
| 10 | [#1241](https://github.com/trivenico/Yuki/pull/1241) | SQLite history importer and composed production runtime | Open |

This list is the current implementation stack, not permission to merge automatically. The earlier WI-6T acceptance and WI-6U architecture dependencies (including #1228–#1232) must also be reviewed in their declared base-branch order. Do not rebase a child onto an unrelated base or merge a child while its parent is unaccepted.

## Merge and acceptance protocol

For each pull request, in dependency order:

1. Confirm the PR head SHA and base branch still match the intended stack.
2. Review the complete diff, migration compatibility, authorization boundaries, rollback behavior, and tests.
3. Require every applicable GitHub Actions check for that exact head SHA to complete successfully. A queued, pending, missing, or stale check is not a pass.
4. Inspect failed jobs and logs; fix defects on the relevant branch and repeat the checks on the new SHA.
5. Confirm the PR remains mergeable and contains no unintended changes.
6. Merge only after explicit release-owner approval. Recheck the child PR's base and mergeability after each parent merge.

A green focused workflow is necessary but does not replace the repository's required quality/security checks. A green aggregate check must correspond to the exact commit under review.

## Clean-database migration gate

Before production use, execute the complete ordered migration set against a disposable, clean PostgreSQL instance:

- `0001_security_work_leases.sql`
- `0002_security_ingestion_cursors.sql`
- `0003_security_alert_outbox.sql`
- `0004_workload_key_registry.sql`
- `0005_security_monitor.sql`
- `0006_authorization_audit.sql`

Record PostgreSQL version, migration runner/version, applied migration identifiers/checksums, database role, and execution output. Verify all expected tables, indexes, constraints, and triggers exist. Re-run the migration process according to the deployment runner's supported idempotency policy and confirm it does not corrupt state. Test the runtime role separately from the migration role and verify it cannot perform schema-owner operations.

The repository's integration test applies these SQL files in order, but that test alone is not evidence of a production migration rehearsal, migration history tracking, rollback, or least-privilege deployment.

## Runtime and security acceptance

Require evidence for all of the following:

- Runtime startup rejects SQLite or incomplete PostgreSQL configuration and closes every partially created pool on failure.
- Missing migrations, connection failures, and repository readiness failures keep the instance out of service.
- Protected operations fail closed during PostgreSQL loss; no local SQLite fallback is allowed in multi-replica mode.
- Authorization audit chain and independently held external anchor match exactly before traffic is enabled.
- SQLite historical import is rehearsed from a quiesced, protected source backup whose external anchor matches the exact source head. Verify imported row count, event IDs, hashes, and target chain; retain the original backup.
- Ingestion lease fencing, replay idempotency, alert outbox retries, provider idempotency, key revocation, and monitor history are tested against real PostgreSQL.
- Operational snapshots are authenticated, payload-free, and unavailable to unauthorized callers.
- Shutdown closes all owned pools and the host keeps an instance out of service if shutdown is incomplete.

## Infrastructure release drill — mandatory, not CI-substitutable

Run against a disposable environment representative of production and retain evidence:

1. Take an encrypted, off-host PostgreSQL backup and record its identifier and restore point.
2. Restore it to an isolated instance without restoring or co-locating the independent audit anchor.
3. Verify schema/migration versions, row counts, workload-key revocation state, monitor history, ingestion cursors, alert outbox state, and the complete authorization audit chain.
4. Verify the restored audit head matches the independently held anchor exactly. A mismatch is a hard stop; never publish a new anchor to bless restored data.
5. Kill an ingestion worker during a page and prove a replacement fences the stale worker and resumes without skipping events.
6. Simulate provider acceptance followed by worker death before outbox acknowledgement; verify retry uses the same stable alert ID and the provider deduplicates it.
7. Simulate database loss and recovery; verify bounded failure, no local fallback, and readiness remains false until integrity checks succeed.
8. Exercise failover and record recovery time, data-loss observations, operator actions, and any manual reconciliation.

Attach the drill report, sanitized logs, backup/restore identifiers, anchor checkpoint, migration versions, and approver sign-off to the release record. Do not represent a planned drill or a mocked unit test as completed infrastructure evidence.

## Rollout sequence

1. Complete review and merge of the dependency stack in order, with all required checks green.
2. Apply migrations using a dedicated migration identity.
3. Configure TLS-verified PostgreSQL connectivity, least-privilege runtime credentials, bounded aggregate connection pools, and independent audit-anchor access.
4. Rehearse historical audit import and verify exact anchor agreement before cutover.
5. Start one production-like instance with protected traffic disabled; verify readiness and authorized operational telemetry.
6. Enable one worker/instance, observe cursor progress, alert backlog, key state, audit integrity, and pool health.
7. Increase replicas gradually while observing lease churn, lock waits, queue lag, dead letters, and provider idempotency.
8. Keep a tested, forward-only recovery plan. Never restore revoked keys, rewind an externally anchored audit chain, or dual-write security state to SQLite and PostgreSQL.

## Release decision record

Production approval requires a named owner and dated evidence for:

- [ ] Dependency PRs reviewed and merged in order
- [ ] Exact-head required GitHub Actions checks green
- [ ] Clean PostgreSQL migration rehearsal complete
- [ ] Runtime fail-closed and authorization-boundary tests accepted
- [ ] SQLite audit import and exact external-anchor verification rehearsed
- [ ] Backup restore and failover drill complete
- [ ] Provider idempotency and worker crash/replay evidence retained
- [ ] Least-privilege roles, TLS, pool budgets, monitoring, and alerting verified
- [ ] Rollback/recovery plan approved
- [ ] Release owner approval recorded

Until every applicable item is evidenced and approved, status remains **NOT APPROVED FOR MULTI-REPLICA PRODUCTION**.
