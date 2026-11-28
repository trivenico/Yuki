# OAuth AES-256 Key-Strength Policy Audit

## Scope

Reviewed at PR #1346 head `ed0a4bc0359681b62d9bb69a8e6e7eedfc151760`:

- `core/oauth_aead.py`
- `core/oauth_os_key_provider.py`
- `core/oauth_runtime.py`
- `tests/test_oauth_aead.py`
- `tests/test_oauth_os_key_provider.py`
- `docs/PHASE173_AES_GCM_CREDENTIAL_CIPHER.md`
- `docs/PHASE174_OS_BACKED_OAUTH_KEY_PROVIDER.md`

Source-level review only; no runtime tests were executed.

## Finding: AES-GCM adapter accepts keys weaker than its documented AES-256 policy

**Severity: Low — cryptographic policy enforcement / configuration drift.**

The cipher is explicitly named and documented as an AES-256-GCM adapter. The documentation describes 32-byte AES keys, and the built-in OS key provider generates exactly 32-byte keys. However, `AESGCMCredentialCipher._validate_key()` accepts key lengths of 16, 24, or 32 bytes.

The `OAuthKeyProvider` protocol is injectable. A production host that supplies a KMS/HSM adapter or another custom provider returning a 16-byte or 24-byte key will therefore be accepted and encrypt credentials with AES-128-GCM or AES-192-GCM rather than the documented AES-256-GCM policy. No warning or explicit policy selection is exposed.

This does not demonstrate a practical break of AES-128 or AES-192, nor does it affect the built-in `OSKeyringOAuthKeyProvider`, which generates 32-byte keys. The defect is that the implementation silently permits a cryptographic configuration inconsistent with its declared security contract.

## Evidence

- `core/oauth_aead.py`: `_validate_key()` currently checks `len(key) not in (16, 24, 32)`.
- `core/oauth_os_key_provider.py`: `_KEY_BYTES = 32`; generated and recovered OS-store keys are exactly 32 bytes.
- `docs/PHASE173_AES_GCM_CREDENTIAL_CIPHER.md`: describes the adapter as AES-GCM and specifies 32-byte AES keys in its key custody contract.
- `tests/test_oauth_aead.py`: tests reject short key material but do not assert rejection of valid AES-128/AES-192 key lengths.

## Recommended remediation

1. If AES-256 is the intended invariant, require `len(key) == 32` in `_validate_key()`.
2. Add tests proving 16-byte and 24-byte provider keys are rejected for both encryption and decryption, while 32-byte keys remain accepted.
3. Keep the policy in one clearly named constant and reference it in documentation and host-provider integration contracts.
4. If multiple AES strengths are intentionally supported, rename/document the adapter as AES-GCM rather than AES-256-GCM and make the selected strength an explicit reviewed policy. Do not silently infer policy from arbitrary provider output.

## Related lifecycle observations (not new findings)

- Rotation retains historical keys, allowing old ciphertext to remain decryptable.
- Rotation changes the active key for new writes; no automated vault re-encryption job or key-retirement workflow is present. The phase documentation explicitly identifies this as a remaining production gate.
- The OS provider caps the historical-key index at 128 and intentionally does not delete historical keys. Without a re-encryption and backup-expiry lifecycle, repeated rotations eventually reach that capacity.
- The OS credential store and SQLite vault require coordinated backup and recovery. The runtime documentation explicitly assigns this responsibility to the host/operator; the generic daily data backup does not itself back up the OS credential store.

These lifecycle limitations are already documented in the phase and backup contracts and are not presented as newly discovered defects here.

## Boundaries

- No evidence was found that the default OS-backed provider emits keys shorter than 32 bytes.
- No claim is made that AES-128 or AES-192 is practically broken.
- No production keyring/KMS integration, backup restore, or runtime test was performed during this review.
