# Audit 069 — Logging, Observability, Audit Integrity & Sensitive Telemetry

**Scope:** logging and exception handling, telemetry/tracing/metrics, API observability, sensitive-data exposure, and approval/MCP audit integrity.

**Review basis:** source inspection of `core/approval_audit.py`, `tests/test_approval_audit.py`, `core/config.py`, `core/default_config.yaml`, `api/runtime.py`, and `api/web.py` on `main`. Candidate conventional logging/telemetry modules and paths were checked but were not present at those exact paths. This is a focused source review, not proof that no logging exists elsewhere in the repository.

## Positive controls observed

- `SQLiteApprovalAuditLog.append` uses `BEGIN IMMEDIATE` and commits event insertion transactionally.
- Actor and pending identifiers are SHA-256 digests at rest; pending bearer IDs are not returned in listed event records.
- Event hashes cover event type, pending ID hash, actor hash, skill ID, timestamp, and previous hash using deterministic JSON serialization.
- `verify_chain()` checks previous-hash continuity and recomputes each event hash using constant-time digest comparisons.
- Existing tests cover persisted events, metadata-only output, plaintext identifier absence, valid chain verification, and tamper detection.
- The inspected API runtime and web shell do not introduce an obvious logging or telemetry sink in the reviewed code.

## Findings and risks

### P1 — Audit integrity is detectable, not storage-enforced or independently anchored

The audit table is created without SQLite triggers or restrictive database-level controls preventing UPDATE/DELETE. The test suite itself demonstrates that a process able to open the database can update an event row. The hash chain detects that mutation when `verify_chain()` is run, but a privileged writer can also delete rows or rewrite the entire chain. A local hash chain without a separately protected signing key or external checkpoint cannot prove history against an actor with database write access.

**Recommendation:** enforce append-only behavior at the database boundary where practical (triggers/authorizer and filesystem permissions), verify on startup and before security-sensitive exports/reads, and periodically anchor signed chain checkpoints to a separate trust domain. Define recovery and alerting for verification failure. Treat local SQLite as tamper-evident against accidental/limited mutation, not immutable against host compromise.

### P1 — Verification is an opt-in operation in the exposed audit API

`list_events()` returns records without invoking `verify_chain()`. The inspected tests call verification explicitly, but this review did not find evidence that normal read/export paths automatically verify integrity. Consumers could therefore display or export tampered events without being warned.

**Recommendation:** expose a verified read/export operation that verifies the complete chain before returning events; make verification status explicit and fail closed for security decisions when verification fails. Avoid silently treating an unverified listing as trusted evidence.

### P2 — No centralized observability/redaction contract was identified in the reviewed paths

The expected conventional modules `core/logging.py`, `core/logger.py`, `core/observability.py`, `core/telemetry.py`, `core/tracing.py`, and `core/metrics.py`, and `api/middleware.py`, were not present at those exact paths. The inspected configuration has no explicit log/trace/exporter retention or redaction settings. This limited inspection does not establish that logging is absent repo-wide; it means a single enforceable contract was not demonstrated in these paths.

**Recommendation:** define a shared structured event schema and mandatory redaction for authorization headers, cookies, access/refresh tokens, API keys, OAuth codes, prompt content, tool arguments/results, and personal data. Keep exception messages and trace attributes bounded; use stable request/trace correlation IDs that do not embed user or secret values. Add sink-level tests with sentinel secrets and CR/LF injection cases.

### P2 — Retention, access control, and lifecycle for audit/telemetry need explicit operational guarantees

The inspected audit log has no retention, export, access-control, or external archival policy in its class contract. Filesystem privacy preparation is present, but it does not itself establish operational retention, backup protection, or separate-reader/writer roles.

**Recommendation:** document retention and deletion rules, encrypt/protect backups, restrict database access by process identity, monitor disk growth and verification failures, and separate operational telemetry from security audit records.

## Remediation decision

No narrow implementation patch was submitted in this audit. The key gaps span database deployment permissions, verification call sites, external checkpoint custody, and repository-wide logging sinks. Adding a local trigger or redaction helper without tracing all writers/readers and operational deployment behavior could create false assurance. Follow-up implementation should be split into:
1. verified audit read/export contract and mandatory verification call sites;
2. database append-only hardening plus tests for UPDATE/DELETE;
3. external signed checkpoint/alerting integration;
4. repository-wide structured logging/redaction inventory and sentinel tests.

## Validation status

- Source inspection completed for the paths listed above.
- Existing tests were read but **not executed**.
- No runtime exploit test, deployment review, log-sink inventory, or external checkpoint integration test was performed.
- No changes to application runtime code were made in this audit.
