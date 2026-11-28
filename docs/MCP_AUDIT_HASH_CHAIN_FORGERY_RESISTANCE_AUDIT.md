# MCP Audit Hash-Chain Forgery Resistance Audit

## Finding

**Severity: Low — the approval/execution audit chain is internally consistent, but not independently tamper-authenticating**

The SQLite audit log computes each event hash with an unkeyed SHA-256 digest over the event fields and previous event hash. `verify_chain()` recomputes those same public hashes and checks linkage. It does not use a secret key, digital signature, or externally retained checkpoint.

Consequently, an actor who can rewrite the audit database can alter, remove, insert, or reorder events and recompute the subsequent hashes. The resulting modified database can still pass `verify_chain()`. The chain detects accidental corruption and unsophisticated edits, but it does not establish authenticity against a database writer.

## Source evidence

- `core/approval_audit.py`, `SQLiteApprovalAuditLog._event_hash()`: event hashes are plain SHA-256 over canonical JSON containing event type, pending-ID hash, actor hash, skill ID, timestamp, and previous hash.
- `SQLiteApprovalAuditLog.append()`: appends a new event using the previous row's hash; no keyed MAC or signature is applied.
- `SQLiteApprovalAuditLog.verify_chain()`: recomputes the same unkeyed digest and verifies the previous-hash link. All inputs required to recompute the chain are stored in the database.
- `SQLiteApprovalAuditLog.reconcile_unresolved_execution()`: relies on `verify_chain()` before accepting reconciliation, so a rewritten chain can also satisfy this integrity precondition.
- The database path is prepared with private SQLite filesystem permissions. This is useful protection against other OS users, but does not cryptographically protect the log from a process or operator with write access to the database and its files.

## Impact and boundaries

An attacker or compromised process with write access to the SQLite database can forge a self-consistent audit history, including removing or changing evidence of policy decisions and tool execution outcomes. An investigator relying only on `verify_chain()` could therefore mistake a rewritten history for an authentic one.

This does **not** demonstrate remote access to the audit database, an authorization bypass, or the ability to alter audit records through the public API. The practical threat depends on the deployment's local filesystem, process, backup, and operator trust boundaries. The current design still provides useful detection of accidental corruption and edits that do not recompute the chain.

## Recommended remediation

1. If adversarial tampering is in scope, authenticate each event with HMAC-SHA-256 using a key stored outside the audit database and protected by the OS-backed key provider or an equivalent secret boundary.
2. Bind the MAC to the event fields and previous authenticated event digest; verify the MAC as well as chain continuity.
3. Consider periodically exporting signed chain checkpoints to an independently controlled append-only destination. This protects against an attacker who can also access the application key store.
4. Define key rotation and backup/restore behavior so historical records remain verifiable without placing the authentication key beside the database.
5. Add tests that modify an event and recompute all unkeyed hashes: the current implementation should demonstrate the limitation, and a keyed/external-anchor implementation should reject the forgery.

Do not describe a keyed chain as protection against compromise of both the database and its authentication key. Independent checkpoints are needed for that threat model.

## Review limitations

Source-only review at PR #1346 head `9d4893ae6afd7cbf58bcffa285b83d81eb02441b`. No runtime tampering exercise, tests, or CI were executed. The finding is about the guarantees of the integrity mechanism, not an observed production incident.
