# WI-5Z — Read-only audit backup verification and recovery fencing

## Problem

A restored audit database can have a locally valid hash chain while omitting events that were written after the backup was taken. A backup is not safe to restore solely because its own chain verifies.

## Changes

- Add SQLiteAuthorizationAuditLog.verify_backup(path, anchor, ...) as a read-only verification operation.
- Require the backup file to exist and contain the expected audit table.
- Verify every row's sequence, previous hash, and event hash.
- Verify the external checkpoint receipt and ensure the backup contains at least the anchored event ID with the exact anchored hash.
- Optionally enforce the same checkpoint freshness budget used by production security composition.
- Never create a missing backup file, initialize an anchor, publish a checkpoint, repair rows, or change the active database path.
- Add tests for a valid backup, a backup older than the external anchor, tampering, missing-file behavior, and read-only preservation.

## Recovery procedure

1. Stop authorization-sensitive writes or place the service into maintenance mode.
2. Preserve the current audit database and candidate backup as immutable evidence; do not overwrite either.
3. Query the independently operated anchor service and record its latest signed/attested checkpoint receipt using the provider's own audit trail.
4. Run verify_backup against the candidate backup and the existing anchor. Require success and review the verification result before restore.
5. If verification fails because the backup is behind the anchor, locate a newer backup or replay missing audit events from a trusted append-only replica. Never lower, delete, or reset the external checkpoint to make the backup pass.
6. Restore to a new path, verify again, then atomically switch the application to the verified database using deployment-specific filesystem/database procedures.
7. Run ProductionSecurityContext.verify_audit_integrity() and GET /health/ready before reopening traffic.
8. Preserve the pre-restore database and recovery evidence under the incident retention policy.

## Fail-closed cases

Missing/corrupt backup, schema mismatch, sequence gap, invalid hash chain, unavailable anchor, malformed checkpoint, backup head older than the anchored event, hash mismatch, or stale checkpoint all reject the backup. The function is read-only and does not attempt repair.

## Limitations

- This PR verifies candidate backup bytes; it does not perform filesystem replacement, stop services, restore SQLite files, replay a replica, or automate disaster recovery.
- The external anchor adapter must be independently operated and provide durable monotonic semantics. The in-process protocol cannot prove the trustworthiness of a particular adapter.
- The current checkpoint timestamp is part of the anchor receipt contract. A production adapter must stamp/attest publication time at the external service rather than blindly trusting a caller-supplied timestamp; provider-specific attestation and credentials remain host integration work.
- The backup must be a consistent SQLite backup (for example, produced through SQLite's backup API or an equivalent quiesced snapshot). Copying a live database file without its WAL state is not certified by this verifier.
- Tests were added but not executed in this environment.
