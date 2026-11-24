# WI-6E — Audit-anchor operations, readiness, and recovery

## Readiness and liveness

`core.audit_anchor_operations.AuditAnchorReadinessASGI` exposes:
- `GET /health/live`: process-level liveness only.
- `GET /health/ready`: verifies the SQLite database is a regular single-link file with private POSIX permissions, runs SQLite `PRAGMA integrity_check`, verifies required schema presence, and validates the stored checkpoint record shape.

Readiness returns HTTP 200 only when the local database checks pass; otherwise it returns HTTP 503 with a generic `not_ready` body. It does not expose filesystem paths, SQL errors, identities, or checkpoint hashes. Responses are non-cacheable. A database with no initial checkpoint can still be locally ready; deployment readiness must separately verify that the initial anchor bootstrap has been completed before accepting production traffic.

These endpoints do not establish end-to-end service health. Orchestration must also check TLS/mTLS, workload identity resolver, credential replay/revocation store, signer/KMS, admin identity provider, and independent audit publication freshness. Consider restricting health routes to the internal monitoring network.

## Consistent online backups

`create_audit_anchor_backup(service, destination_path)` uses SQLite's online backup API to capture a consistent snapshot while the service may be receiving requests. It does not byte-copy an active database. The backup is written to a same-directory temporary file, checked with SQLite `integrity_check` and required-schema validation, fsynced, and atomically replaced into the destination. The resulting file is private (owner-only on POSIX). A SHA-256 digest, byte size, and checkpoint event ID are returned for inventory/monitoring.

The destination must be a separate path from the live database. Failed backup creation does not replace an existing destination. Store backups in a separate failure domain with independent access controls, encryption at rest, retention, and immutable/versioned object storage. The local helper does not upload backups or provide immutable storage.

## Recovery procedure

1. Stop or isolate the anchor service before a restore.
2. Select a backup from independently controlled storage and verify its recorded digest.
3. Restore into a new private database path; never overwrite the only known-good copy.
4. Run SQLite integrity and schema checks.
5. Reconcile the restored checkpoint against the independent audit publisher's expected external head. Do not reset or roll back the external checkpoint to make a mismatched restore pass.
6. Start the service with the trusted identity resolver, replay/revocation backend, signer, and administrator controls.
7. Confirm readiness, test publisher read/publish with a controlled credential, verify revocation, and confirm the independent publisher can advance/refresh its checkpoint.
8. Preserve the pre-restore database and incident evidence until the recovery is accepted.

A SQLite backup is only a consistent copy of the anchor database. It is not itself proof that the stored checkpoint is trustworthy; compare it with the independent Yuki audit chain and external checkpoint as part of recovery.

## Deployment limits

This milestone supplies code-level operational primitives. It does not provision a backup destination, encryption keys, immutable object storage, monitoring, alert routing, HA database, multi-region replication, KMS/HSM, or infrastructure network policy. SQLite remains a single-node/local-filesystem service; do not place its live database on an unsupported network filesystem or share it between independent writers across regions. Perform restore drills and test the actual storage/retention system before production.
