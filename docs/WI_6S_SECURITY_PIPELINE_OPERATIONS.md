# WI-6S — Security pipeline deployment and operations runbook

## Scope and readiness statement

This runbook describes how a host deployment should operate the security pipeline components introduced in WI-6K through WI-6R. It is an operator procedure, not a deployment manifest or proof that a production host is configured. The repository does not currently supply a complete production identity/policy bootstrap, managed scheduler, external SIEM credentials, or multi-host coordinator. Do not expose private health endpoints publicly or infer production readiness from the presence of these modules.

The reference implementation uses multiple local SQLite databases and in-process worker threads. Treat it as a single-host deployment unless a separately reviewed shared-state architecture replaces those assumptions.

## Deployment prerequisites

Before enabling workers, the deployment owner must verify:

- A dedicated service identity and host-level authorization are configured for operator/admin actions.
- Production policy and identity dependencies are supplied by trusted host bootstrap; no identity, tenant, permission, or policy revision is accepted from user-controlled configuration or request payloads.
- Authorization audit checkpoints are anchored in the independently administered trust domain. The external anchor and publisher credentials are not stored beside Yuki databases.
- Provider/SIEM credentials are held in a secret manager or OS credential store, with least-privilege scopes and rotation procedures.
- All database paths resolve to private, persistent storage with restrictive filesystem permissions. The volume is encrypted at rest where supported and included in monitored backup coverage.
- Network egress is allow-listed. Provider clients enforce TLS verification, bounded connect/read timeouts, response-size limits, and redirect policy.
- The host process manager enforces a single active worker owner for each local SQLite deployment, restarts failed processes, and applies a hard process shutdown deadline.
- Disk capacity, database growth, backup retention, and alert delivery ownership are assigned to operational owners.

Do not invent environment variable names for these components: host bootstrap owns its configuration contract and must validate it before constructing services.

## Startup sequence

1. Mount the private data volume and verify ownership, permissions, available space, and expected database paths.
2. Load secrets from the deployment secret manager. Do not print configuration objects or secrets in startup logs.
3. Construct the trusted identity resolver, policy engine, authorization gateway, durable audit writer, and independent audit anchor.
4. Verify the local authorization audit chain against the external checkpoint. Fail closed if the anchor is missing, stale, unavailable, or inconsistent.
5. Construct the security event monitor, ingestion cursor store, alert outbox, heartbeat store, and health aggregator using their intended persistent database paths.
6. Verify configured ingestion adapters and alert sender using bounded, non-mutating checks. Do not send synthetic production alerts unless the provider has a designated test destination.
7. Start supervised workers only after dependencies and private storage are ready. Keep operations bounded and use database/network timeouts shorter than the process shutdown budget.
8. Confirm worker heartbeats, cursor freshness after an actual successful ingestion page, and outbox dispatch progress. A heartbeat alone does not prove source progress.
9. Keep the private security-operations health endpoint behind host authentication and a private listener. Do not expose it through a public reverse proxy.

A failed prerequisite must prevent worker startup or keep readiness false. Never silently fall back to a development policy, in-memory audit anchor, or unauthenticated health listener.

## Runtime monitoring and alerting

Poll the authenticated /health/security-operations endpoint from a private monitoring network. It is a readiness/operations signal, not a replacement for host, dependency, or external-anchor monitoring.

Configure alerts for:
- Missing or stale worker heartbeats.
- Required ingestion cursors that are missing, never initialized, stale, or reporting a source error.
- Oldest pending outbox age exceeding the delivery SLO.
- Increasing inflight lease expirations, repeated sender failures, or any dead-letter count above zero.
- Audit checkpoint publisher failures or checkpoint age approaching its configured freshness limit.
- SQLite busy/locked errors, disk-space pressure, database integrity failures, backup failures, and process restarts.
- Repeated sanitized worker failure events. The observer receives allow-listed metadata and a generic failure code, not exception text.

Use separate alerts for liveness and progress. Quiet source streams can be healthy; validate cursor freshness and worker heartbeat rather than assuming that no new events means ingestion is broken. A live heartbeat also does not prove that the cursor advanced.

The outbox provides at-least-once delivery. Provider integrations should use the stable alert_id idempotency key where supported and operators should expect a duplicate notification if a process crashes after remote acceptance but before local completion.

## Graceful shutdown and restart

1. Remove the instance from traffic and stop scheduling new host-level work.
2. Request supervisor shutdown with a bounded timeout.
3. Inspect the returned per-worker stopped state. A false value means at least one callback did not return before the deadline; do not report a clean shutdown.
4. Allow bounded database/network calls to finish. Python threads cannot forcibly interrupt a blocked callback.
5. If the process deadline is reached, let the process manager terminate the process. On restart, rely on cursor leases and outbox leases/fencing to prevent stale workers from committing progress.
6. Confirm the new process has fresh heartbeats, cursor progress, and normal outbox state before restoring traffic.

Do not run two independent processes against the same local SQLite worker state unless exclusive ownership and locking behavior have been explicitly validated.

## Backup procedure

Back up every security-pipeline SQLite database, including the authorization audit log, security event monitor, ingestion cursors, alert outbox, worker heartbeats, workload-key registry, and any separately deployed anchor-service database. Use the actual host-bootstrap inventory; do not assume all components share one database.

1. Quiesce workers and writers, or use SQLite's supported online backup API for each database.
2. Capture each database using a SQLite-consistent backup method. Do not copy only the main database file while it is live; WAL contents may contain committed data.
3. Record backup time, application version, schema/migration version, database identity, and cryptographic checksum. Never include secrets in the catalog.
4. Encrypt backups, restrict access, and store copies in a separate failure domain with retention and deletion controls.
5. Verify backup readability and run restore drills in isolation. A successful file copy is not a verified backup.

The independently administered audit anchor must be backed up under its own trust-domain procedures. Never restore an older external anchor over a newer checkpoint.

## Restore and recovery procedure

1. Isolate the restored instance from public traffic and stop all workers.
2. Restore each database from a known consistent backup into private paths. Preserve filesystem permissions and verify SQLite integrity with PRAGMA integrity_check before startup.
3. Compare restored authorization audit history with the independent external checkpoint. If local history is behind the anchor, mismatched, or unavailable, keep production readiness false. Do not lower, reset, or recreate the external checkpoint to make the restore pass.
4. Restore the security monitor, cursor, and outbox databases from a mutually understood recovery point. Separate SQLite databases do not provide one atomic cross-database snapshot.
5. Expect replay from the last committed ingestion cursor. Deterministic signal IDs are intended to make replay idempotent. Verify re-import does not create duplicate incidents.
6. Inspect pending, inflight, delivered, and dead outbox states. Expired claims may be reclaimed. Use provider idempotency keys to limit duplicate external notifications.
7. Reconcile workload-key registry state with independently controlled key-management records before accepting credentials. Never reactivate revoked keys merely to match an old backup.
8. Start workers in the documented order and verify health, cursor progress, outbox progress, and checkpoint freshness.
9. Record the recovery point, recovery time, operator actions, discrepancies, and manually reconciled records.

There is no cross-database atomic restore guarantee. If databases cannot be reconciled confidently, keep the service offline and escalate rather than inventing cursor or incident state.

## Deployment rollback

Rollback means reverting the application artifact to a previously reviewed version while preserving forward security state. It does not mean restoring old authorization history, rolling back the external audit checkpoint, or reactivating revoked credentials.

Before rollback, stop workers and preserve a forensic backup. Confirm schema compatibility before starting the older application. If a migration is not backward-compatible, use the documented forward-fix path or a separately tested data migration; do not downgrade database schemas by guesswork. Keep the external audit anchor monotonic.

After rollback, repeat audit integrity verification, key-state reconciliation, worker liveness/progress checks, and outbox review before restoring traffic.

## Production acceptance checklist

A release owner should record evidence for each item:

- [ ] Trusted production identity and policy bootstrap; missing dependencies fail closed.
- [ ] Durable audit chain verifies against an independent, fresh checkpoint.
- [ ] Private database paths, permissions, encryption, retention, and disk alerts verified.
- [ ] Worker startup, partial-start rollback, blocked-operation shutdown, and process restart tested.
- [ ] Ingestion replay, cursor lease expiry/fencing, and recovery tested against real SQLite stores.
- [ ] Outbox retry, lease expiry, dead-letter, and provider idempotency behavior tested.
- [ ] Private health endpoint authentication and unhealthy/healthy transitions tested.
- [ ] Backup and isolated restore drill completed, including external-anchor reconciliation.
- [ ] Alert routing, on-call ownership, and incident escalation tested.
- [ ] Full CI suite and targeted security tests pass on the exact release commit.
- [ ] Rollback procedure rehearsed without rolling back the external checkpoint or revoked-key state.
- [ ] Security review and deployment sign-off recorded.

An unchecked item is a release blocker unless the release owner documents a formally accepted risk with an expiry and compensating control.

## Known limitations

- Worker orchestration is in-process and single-host; no distributed scheduler or leader election is provided.
- Python cannot terminate a blocked worker callback; host I/O timeouts and process supervision are mandatory.
- SQLite stores are separate and do not form a cross-database transaction.
- The outbox is at-least-once, not exactly-once.
- The repository does not deploy the remote SIEM, secret manager, independent audit anchor, identity provider, policy service, or host process manager.
- This runbook does not itself establish that a deployment has completed these steps.