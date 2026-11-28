# OAuth Credential Vault Storage Growth and Contention Audit

## Scope

Source review at PR #1346 head `8c037c3ba57da451097af73bf32625f67a18a9cb`. Reviewed `core/oauth_credential_vault.py` and `tests/test_oauth_credential_vault.py`, with focus on token bounds, encryption placement, SQLite transaction duration, row lifecycle, and enumeration behavior.

## Finding: credential rows and owner account enumeration are not quota-bounded

**Severity: Medium (availability / storage exhaustion; practical exposure depends on authorization and provider account constraints).**

The vault's primary key is (owner_id, provider_id, account_id). Each identifier is bounded to 256 characters and token values are bounded to 8,192 characters. Encrypted token blobs are capped at 16,384 bytes. These limits bound an individual row's variable fields, but there is no maximum number of rows per owner, provider, or database.

Every successful `store()` for a new account tuple inserts another durable row. The vault has no automatic expiration cleanup: `expires_at` is stored as metadata, but expired rows remain until an explicit disconnect/delete path removes them. There is no global row cap, per-owner connection quota, age-based retention policy, or storage-pressure admission check in this module.

The unbounded row count also affects read work. `list_accounts(owner_id=...)` fetches every matching row and materializes the complete result in memory; there is no pagination or maximum result size. A principal with many connected account identifiers can therefore cause growing database storage and increasingly expensive account enumeration. The exact ability to create many distinct account IDs depends on provider-side identity semantics and host authorization policy; the vault itself does not enforce a cap.

## Contention and crypto review

No separate high-confidence lock-duration defect was identified in the inspected methods:

- `store()` and `rotate()` perform token validation and encryption before opening the write transaction.
- `get()` reads the ciphertext and closes the SQLite connection before decryption.
- `BEGIN IMMEDIATE` write sections contain database reads/writes and commit/rollback, not remote provider calls or AEAD operations.
- Connections use a five-second SQLite timeout and `busy_timeout=5000`.
- Token and ciphertext sizes are bounded, limiting per-row cryptographic work.

The five-second busy timeout means callers can still wait under contention, and SQLite's single-writer model remains a throughput constraint, but this review did not establish a lock held across external network calls or an unbounded crypto input path.

## Evidence

- `core/oauth_credential_vault.py`: table primary key is (owner_id, provider_id, account_id); no row-count/quota enforcement exists in constructor, `store()`, or `rotate()`.
- `store()`: inserts a new credential row for each previously unseen account tuple.
- `expires_at` is validated and stored, but no expiry cleanup or pruning method exists in the vault.
- `list_accounts()`: executes an unpaginated query for all accounts owned by the requested owner and calls `fetchall()`, then materializes the full result list.
- `_token()` caps tokens at 8,192 characters and `_encrypt()` caps ciphertext at 16,384 bytes.
- `store()`/`rotate()` encrypt before `BEGIN IMMEDIATE`; `get()` decrypts after its read connection closes.
- `tests/test_oauth_credential_vault.py` covers encryption, owner isolation, refresh CAS, disconnect, validation, and tamper rejection; it does not test row quotas, expired-row retention, pagination, or database growth limits.

## Recommended remediation

1. Enforce a configurable per-owner and per-provider maximum connected-account count in the durable vault transaction, not solely in an API process-local counter.
2. Add a global storage safety ceiling and operational monitoring for row count, database size, WAL size, busy/locked errors, and list latency.
3. Define an explicit expired-credential lifecycle. Prefer a bounded maintenance job with auditable deletion/retention policy; do not silently delete credentials if product semantics require reconnect or user-visible disconnect.
4. Make account enumeration paginated with a stable cursor and bounded page size. Preserve deterministic ordering and avoid loading all rows into memory.
5. Ensure quota checks and inserts are serialized in one transaction so concurrent authorization completions cannot exceed the configured limit.
6. Add tests for concurrent stores at the quota boundary, expired-row retention/cleanup, pagination under large datasets, and graceful behavior when the global storage ceiling is reached.

## Boundaries and non-findings

- This is not a token confidentiality or cross-owner isolation finding. The inspected code uses AEAD associated data and owner-scoped SQL predicates.
- Individual token and ciphertext sizes are bounded.
- No external network call or encryption operation was observed inside the SQLite write transaction.
- SQLite contention is possible under concurrent writes, but this review did not establish an unbounded transaction duration or a lock-order deadlock.
- The vault may be protected by host-level connection quotas or deployment-specific storage controls; those controls were not established from this module review.
- No runtime code was changed and no tests were executed as part of this source review.
