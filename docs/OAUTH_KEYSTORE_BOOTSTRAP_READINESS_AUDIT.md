# OAuth Key-Store Bootstrap Readiness Validation Audit

## Scope

Reviewed at PR #1346 head `30c5d950ee7998bf5848141e341a484b337f1113`:

- `core/oauth_os_key_provider.py`
- `scripts/initialize_oauth_keys.py`
- `core/oauth_runtime.py`
- `tests/test_oauth_os_key_provider.py`
- `docs/PHASE174_OS_BACKED_OAUTH_KEY_PROVIDER.md`
- `docs/PHASE175_OAUTH_KEY_VAULT_RUNTIME_WIRING.md`

Source-level review only. No runtime tests or native OS credential-store operations were executed.

## Finding: idempotent initialization reports readiness without validating required key-store state

**Severity: Low — operational integrity / credential availability.**

When the provider marker already equals `ready`, `OSKeyringOAuthKeyProvider.initialize()` calls `_require_ready()` and checks only that the HMAC entry is present (not that it decodes to a valid 32-byte key), then checks only that the active-key pointer is non-null. It does not validate that:

- the active key ID has the expected format and is present in the historical index;
- the historical index exists and is structurally valid;
- the active key record exists, decodes, and contains exactly 32 bytes; or
- the HMAC record decodes to exactly 32 bytes.

The explicit operator command `scripts/initialize_oauth_keys.py` treats a normal return from `initialize()` as proof of readiness and prints `Yuki OAuth key store is initialized and ready.` It exits successfully even when one of those required records is corrupt or missing.

The later runtime path does validate these items: `provider.hmac_key()` validates HMAC encoding/length, while `provider.current_key()` validates the active pointer, index membership, and active key material. Consequently, the bootstrap command can report success while actual vault construction or credential encryption/decryption immediately fails. This can mislead operators during recovery or deployment validation and delay detection of a damaged key store.

This is distinct from the previously documented ability of `rotate()` to publish a replacement when the active key is damaged. It concerns the bootstrap/readiness contract and the operator-facing success signal, not silent key replacement or direct credential disclosure.

## Evidence

- In `initialize()`, the already-ready branch calls `_require_ready()`, then checks only `_USERNAME_ACTIVE is None`.
- `_require_ready()` checks marker equality and HMAC-entry presence only.
- `_key_index()` validates index structure, but is not called in the already-ready initialization branch.
- `current_key()` performs the active ID/index/key-material validation that the bootstrap path omits.
- `hmac_key()` performs the HMAC base64 and 32-byte validation that the bootstrap path omits.
- `scripts/initialize_oauth_keys.py` prints a ready message and returns exit code 0 whenever `initialize()` returns normally.
- Existing tests cover interrupted bootstrap, missing active pointer via `current_key()`, and corrupt HMAC via `hmac_key()`; they do not exercise the already-ready `initialize()` path with these corrupted states or assert the CLI's readiness result.

## Recommended remediation

1. Make the already-ready initialization branch run a shared, read-only full integrity validator equivalent to the checks required by `current_key()`, `hmac_key()`, and `_key_index()`.
2. Keep validation non-destructive: never regenerate missing keys or rewrite the index/HMAC entry during an idempotent readiness check.
3. Return a sanitized integrity error for missing/malformed index, invalid active pointer, missing/malformed active key, or invalid HMAC key.
4. Add tests that set the marker to `ready` and independently corrupt/remove each required item, then assert `initialize()` fails without modifying any backend entries.
5. Add CLI-level tests proving exit code 0 and the ready message are emitted only after complete validation; corrupted states must return nonzero and must not print a success message.

## Boundaries

- This finding does not claim that the runtime vault accepts the malformed states; the runtime factory's key accessors fail closed.
- It does not claim key material is overwritten or exposed.
- It does not duplicate the existing audits for HMAC-key continuity across backup restore, key rotation active-key validation, cross-process serialization, or automated vault re-encryption.
- No production OS credential store was modified and no runtime tests were executed.
