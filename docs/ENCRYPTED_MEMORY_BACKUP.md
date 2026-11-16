# Encrypted memory backup envelope

`core.memory_backup_envelope` provides a versioned, password-encrypted container for opaque memory backup bytes.

- Format identifier: `yuki-memory-backup`; envelope version: `1`.
- Key derivation: scrypt (`N=16384, r=8, p=1`) with a fresh 16-byte random salt. The work factor stays within common OpenSSL backend memory ceilings.
- Encryption: AES-256-GCM with a fresh 12-byte nonce and authenticated format/version data.
- The encrypted payload is capped at 8 MiB; the encoded envelope is capped at 12 MiB.
- Passwords must be 12–1024 UTF-8 bytes. A wrong password, altered ciphertext, unsupported version, or modified KDF parameters fails closed.
- The module never writes plaintext or falls back to weaker encryption.

The `cryptography` package is already declared in the optional `production-storage` extra. Install that extra to enable this feature; deployments without it receive `memory_backup_crypto_unavailable`.

This is the encryption primitive only, not a complete backup workflow. It does not read the memory database, decide which owner is authorized, define the snapshot schema, or restore records. The next integration layer must derive owner identity from the authenticated session, serialize a bounded versioned snapshot, encrypt it before returning it, and validate/decrypt fully before any restore transaction. A SHA digest alone is not a substitute for this authenticated encryption.

Treat the passphrase as the recovery secret: Yuki cannot recover a lost passphrase. Store it separately from the exported envelope. Never log passphrases, plaintext snapshots, or decrypted payloads.
