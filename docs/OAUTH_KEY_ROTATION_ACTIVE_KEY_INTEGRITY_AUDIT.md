# OAuth Key Rotation Active-Key Integrity Audit

## Scope

Reviewed at PR #1346 head `3c6a01287853c1dfa4a7d5977f7e5211aa487dbd`:

- `core/oauth_os_key_provider.py`
- `core/oauth_aead.py`
- `tests/test_oauth_os_key_provider.py`
- `tests/test_oauth_aead.py`
- Existing OAuth key-provider and vault lifecycle audit reports.

Source-level review only. No runtime tests or production credential-store operations were executed.

## Finding: AES key rotation can publish a replacement without validating the current active key

**Severity: Low — recovery integrity / credential availability.**

`OSKeyringOAuthKeyProvider.rotate()` validates that the provider is marked ready and that the historical key index is structurally valid. It then creates a new key, appends its ID to the index, and publishes that ID as active. It does not first validate that the existing active-key pointer is present, belongs to the index, and resolves to valid key material.

By contrast, `current_key()` explicitly validates the active pointer against the index and calls `get_key()` to validate and retrieve its material. Thus, if the active pointer is missing, malformed, references an unindexed ID, or references a missing/corrupt key item, ordinary encryption fails closed, but a direct call to `rotate()` can still succeed and replace the active pointer.

This does not recover ciphertext encrypted with the unavailable historical key. Existing credentials using that key remain undecryptable. However, the successful rotation can make a damaged key-store state appear operational again for new writes, while the older credentials remain inaccessible. That weakens the provider's stated fail-closed recovery behavior and can obscure the need for coordinated key-store restoration.

The issue is distinct from the already documented lack of an automated vault re-encryption/key-retirement job and from cross-process rotation serialization. It concerns validation of the current key state at the rotation boundary.

## Evidence

- `OSKeyringOAuthKeyProvider.current_key()` checks that the active key ID is a member of `_key_index()` and resolves it through `get_key()`.
- `OSKeyringOAuthKeyProvider.rotate()` calls `_key_index()`, creates and stores a new key, updates the index, and updates the active pointer without performing the equivalent active-key validation.
- `_key_index()` validates index structure and provider readiness, but does not validate the active pointer or the presence/length of every indexed key item.
- Existing tests cover a missing active pointer failing through `current_key()`, and successful rotation retaining historical keys. They do not assert that rotation itself refuses to proceed when the current active key is missing, invalid, or unavailable.

## Recommended remediation

1. Before generating or publishing a replacement key, call the same validation path used by `current_key()` (or a shared private validator) to confirm the current active pointer is valid and its key material is retrievable.
2. Keep rotation fail-closed on missing/corrupt active state. Require an explicit operator recovery procedure rather than allowing rotation to act as an implicit repair.
3. Add tests for `rotate()` with a missing active pointer, an active ID absent from the index, and a missing or malformed active key item. Assert that rotation raises a sanitized integrity error and does not modify the index, active pointer, or create/publish a replacement key.
4. Preserve the existing successful-rotation test proving that the previous key remains available for decrypting old ciphertext.

## Boundaries

- This finding does not claim that rotation deletes or overwrites historical key material.
- It does not claim that a successful rotation makes old ciphertext decryptable.
- It does not duplicate the existing findings on HMAC-key continuity, metadata authentication, AES key length policy, cross-process rotation coordination, or missing automated vault re-encryption.
- No production OS credential store was modified and no runtime tests were executed.
