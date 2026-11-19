# Phase 109 — authenticated backup-pruning approval and audit contract

## Status

Design checkpoint only. This document does not change runtime behavior and does not make Phase 108 production-ready.

## Existing components to reuse

- `automation.authorized_read_tools.ToolPrincipal` is the authenticated principal value object. The caller must construct it from trusted authentication state; request JSON is not an identity source.
- `SQLitePendingApprovalStore` provides expiring, actor/session-bound pending records and atomic one-time consumption.
- `SQLiteApprovalAuditLog` provides private SQLite persistence, hashed actor and pending identifiers, append-only event insertion, and hash-chain verification. Its append operation must succeed before a destructive action is dispatched.

Do not introduce a second principal type, approval identity system, or unrelated audit database unless a concrete schema limitation requires it.

## Required operation boundary

Add a dedicated orchestration service around `prune_sqlite_backup_sets`. The low-level Phase 108 function remains an explicitly destructive primitive and must not be exposed directly to an untrusted API or agent tool.

The orchestration service must:

1. Require a `ToolPrincipal`, a trusted-principal resolver, a bounded session identity, and the explicit `backup:prune` permission.
2. Rebuild a read-only retention plan using the exact inventory, `keep_last`, and pinned IDs supplied for the operation.
3. Construct a canonical operation fingerprint covering:
   - backup-root device/inode identity and normalized location;
   - exact database inventory names, normalized paths, and source file identities;
   - `keep_last` and pinned set IDs;
   - ordered verified, retained, and eligible set IDs;
   - SHA-256 of every verified set manifest.
4. Persist a pending approval snapshot containing the fingerprint and exact approved candidate IDs. Bind it to the authenticated actor and current session using `SQLitePendingApprovalStore`.
5. Append a `requested` audit event before invoking the approval provider. Approval must be bound to the same actor, one-time pending ID, and fingerprint. Provider errors or non-true results mean denial.
6. Append an `approved` event before any destructive operation. Audit failure means no pruning.
7. Atomically consume the pending approval for the same actor/session, reject replay, and compare the consumed snapshot with the exact operation about to execute.
8. Rebuild the retention plan and fingerprint immediately before pruning. If any root identity, inventory identity, manifest digest, candidate eligibility, keep count, or pin changes, abort and require fresh approval.
9. Append `execution_started` before calling Phase 108. Call the low-level function with only the exact approved IDs and `explicit_approval=True`.
10. Append `executed` or `execution_failed` with the same operation correlation. If the outcome audit fails after the filesystem operation, raise an explicit outcome-unknown error and require operator reconciliation; never blindly retry.

Use the existing audit event vocabulary. Encode the operation fingerprint in a bounded, non-sensitive audit skill identifier (for example, a fixed prefix plus the full SHA-256 digest); never place raw backup paths, manifest contents, database contents, approval bearer IDs, or un-hashed actor identifiers in audit payloads.

## Failure and recovery semantics

- Fail closed when principal validation, pending-store persistence/consumption, approval, or any pre-operation audit write fails.
- A consumed approval is never reusable, even if pruning subsequently fails.
- A failure after quarantine or partial unlink is not transactionally reversible. Record failure/outcome-unknown when possible, preserve the quarantine directory, and require operator investigation.
- If post-operation audit persistence fails, the operation may already have completed. Report outcome unknown; do not automatically rerun.
- The audit hash chain detects accidental or unauthorized record modification only while its trust anchor and database remain protected. It is not tamper-proof against an attacker controlling the service account and audit database.
- The same-service-account concurrent-writer limitation documented in Phase 108 remains.

## Required tests before production integration

- Untrusted principal, missing permission, invalid session, and malformed inputs are rejected.
- Approval callback is bound to the exact fingerprint and actor.
- Denied/raising approval callback causes no prune.
- Failure to append requested, approved, or execution-started audit event causes no prune.
- Approval cannot be consumed by another actor or session.
- Approval can be consumed exactly once, including concurrent replay attempts.
- Changed manifest, inventory identity, backup-root identity, pin list, keep count, or eligible set invalidates approval.
- Only the exact approved IDs are passed to the pruning primitive.
- Success, low-level failure, and post-operation audit failure produce distinct, documented outcomes.
- Audit records contain no raw actor, session, pending bearer token, backup path, or database payload.
- Audit chain remains verifiable across service restarts.

## Scope exclusions

No scheduled pruning, background cleanup, automatic approval, API endpoint, permission escalation, production cutover, or bypass of Phase 108 filesystem checks is introduced by this phase.
