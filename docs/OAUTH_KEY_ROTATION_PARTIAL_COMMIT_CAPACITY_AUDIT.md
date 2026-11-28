# OAuth Key Rotation Partial-Commit Capacity Exhaustion Audit

## Scope

Reviewed at PR #1346 head `bacda63cb125b17959774db454b62b715691e836`:

- `core/oauth_os_key_provider.py`
- `tests/test_oauth_os_key_provider.py`
- `docs/PHASE174_OS_BACKED_OAUTH_KEY_PROVIDER.md`
- Existing OAuth key rotation and key-store lifecycle audit reports.

Source-level review only. No runtime tests or native OS credential-store operations were executed.

## Finding: failed active-pointer publication permanently consumes historical-key capacity

**Severity: Low — key-rotation availability / persistent operational exhaustion.**

`OSKeyringOAuthKeyProvider.rotate()` performs a multi-record rotation in this order:

1. Write the newly generated key record.
2. Publish a historical-key index containing the previous entries plus the new key ID.
3. Publish the new active-key pointer.

The backend interface exposes individual `set_password()` calls, not a transaction spanning these records. If the third write fails after the first two succeed, the old active pointer remains in place, but the new key ID remains in the historical index. The method raises a sanitized write error and has no rollback or retry/reconciliation mechanism.

A subsequent rotation treats that unused key ID as a legitimate historical key and appends another ID. The provider imposes a hard maximum of 128 index entries and refuses rotation at that limit. Therefore, repeated failures specifically at active-pointer publication can consume the remaining index capacity while leaving the old key active. Once the index reaches 128 entries, rotation is permanently unavailable through the normal API, even if the credential-store backend has recovered.

The unused keys are not needed to decrypt any ciphertext because the active pointer was not advanced and the failed call did not return the new key ID to a successful caller. They nevertheless remain indexed and cannot be removed through the provider API. This is a persistent availability failure caused by partial commit, not a cryptographic confidentiality or authentication bypass.

This is narrower than the documented lack of automated key re-encryption/key retirement: it is a rotation failure-recovery defect that can exhaust the fixed index capacity without any successful rotations.

## Evidence

- `rotate()` writes the key record, then writes `_USERNAME_INDEX`, then writes `_USERNAME_ACTIVE`.
- The comment says a partial failure leaves the old active key, but the implementation does not restore the prior index if active-pointer publication fails.
- `_key_index()` accepts the appended key ID on the next call; it does not distinguish a staged key from a key used to encrypt data.
- `rotate()` rejects further attempts when `len(index) >= 128`.
- The provider has no delete/remove operation for an unused key record or index entry.
- Existing tests cover successful rotation and generic backend write failure during initial bootstrap, but do not inject a failure specifically at the active-pointer write after index publication and repeat the operation to measure index growth/capacity exhaustion.

## Recommended remediation

1. Introduce an explicit staged/pending rotation state or a versioned rotation manifest so an interrupted publication can be reconciled safely.
2. On retry, detect a pending key whose ID is indexed but not active, validate its key material, and either safely complete publication or retire it under an explicit recovery policy. Do not blindly append another key.
3. If rollback is chosen, restore the previous index only after verifying the active pointer still references the old key; account for ambiguous backend write outcomes where a write may have committed despite raising.
4. Add fault-injection tests for failures at each of the three writes, including a backend that commits a value and then raises. Reconstruct the provider after each failure and verify recovery is deterministic and never loses access to historical keys.
5. Add a repeated-failure regression test proving that failed rotations do not monotonically consume the 128-entry index capacity.
6. Keep cross-process serialization as a separate requirement; a process-local lock does not make the multi-record update transactional across processes.

## Boundaries

- This finding does not claim that an interrupted rotation changes the active key when the active-pointer write definitely fails.
- It does not claim that old ciphertext becomes undecryptable due to this specific partial-commit sequence; the old active key remains indexed.
- It does not duplicate the active-key precondition finding, HMAC-key backup continuity, or missing automated vault re-encryption/key retirement.
- No production OS credential store was modified and no runtime tests were executed.
