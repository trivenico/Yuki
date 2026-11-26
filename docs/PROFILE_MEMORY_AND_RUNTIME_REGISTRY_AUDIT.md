# Profile Memory, Installed Skill Packs, and Runtime Registry Audit (2026-11-26)

## Scope

Read-only review at PR branch `research/reference-projects-integration-assessment`, head `31a8fa71da281bf8291ef646bd031cfabad63f2e`. Inspected `companion/memory.py`, `companion/secure_memory.py`, `companion/memory_consent.py`, `ui/desktop/memory.py`, `core/profile_identity.py`, `core/skill_packs.py`, `core/oidc_jwks_cache.py`, `core/oauth_provider_registry.py`, `core/platform_registry.py`, `skills/registry.py`, and `defense/security/osint_audit.py`. This is a source audit only; no profile files, keyring entries, installed packs or databases were opened or changed; no tests were run locally.

## Confirmed persistent domains

### Encrypted desktop memory

- `ui/desktop/memory.py:create_desktop_memory()` places encrypted memory beneath `desktop_data_root()`, after `prepare_private_desktop_root()`, and obtains a stable profile UUID from `profile_identity.json`.
- `EncryptedMemoryStore` uses AES-256-GCM and stores the 32-byte data-encryption key in the OS keyring under a profile-scoped account. It fails closed if a secure keyring backend is unavailable and does not silently fall back to plaintext.
- The ciphertext envelope is versioned and uses fixed AAD `yuki-companion-memory:v1`. The implementation bounds file size and validates decrypted entries.
- The encrypted store subclasses `MemoryStore`, so explicit add/list/delete/clear operations use the inherited cross-process per-file lock around read/modify/write. The encrypted override uses a temporary file, file fsync and atomic replace.
- Encrypted backup has a deliberate key path: `export_key_for_encrypted_backup()` returns profile-bound key metadata for inclusion only inside an encrypted archive; import verifies the profile ID, existing destination key, encrypted file and authentication tag before installing into the OS keyring.
- Consent is session-scoped. Writes require explicit `enable()`; context inclusion separately requires `enable_context()`. Disabling stops future writes/context use but does not delete existing data.

**Gaps / required validation**
1. Backups must bind the ciphertext, profile UUID, keyring account/key version and encrypted key metadata as one restore unit. Test lost keyring, wrong profile, pre-existing conflicting key, interrupted import and archive rollback.
2. The profile identity file is separately persisted under the desktop root. Backup and restore must preserve the exact identity or explicitly re-key and re-encrypt; silently generating a new profile ID will break key lookup.
3. `EncryptedMemoryStore._save()` creates its parent directory directly. Desktop composition validates the desktop root first, but alternate constructors and custom paths need a documented trust contract or the shared private-profile validator.
4. The generic `CompanionMemory` environment-selected path can still choose plaintext `MemoryStore` unless encryption is enabled; desktop composition explicitly injects encrypted storage. Do not conflate the desktop guarantee with every non-desktop caller.
5. Existing encrypted-memory backup logic must be tested together with the general desktop backup restore and profile migration path. No global maintenance gate currently coordinates memory with other stores.

### Profile identity

- `core/profile_identity.py` stores a stable UUID in `profile_identity.json`, validates a regular non-symlink single-link file, uses no-follow open where available, bounds reads, and atomically publishes a new identity without replacing an existing file.
- POSIX creation sets mode 0600 and fsyncs file and directory.
- This identity is not a secret, but it is a critical key namespace for profile-scoped memory encryption and should be retained during backup/restore.

### Installed skill packs

- `core/skill_packs.py` persists data-only JSON skill pack files under `data_root()/skill_packs`.
- Packs are bounded and schema-validated. JSON duplicate keys are rejected; SKILL.md YAML uses SafeLoader, rejects duplicate keys/anchors/aliases and only accepts a bounded frontmatter schema.
- Third-party SKILL.md imports are forced to high risk and approval-required; the data-only pack cannot carry Python, shell commands, tool implementations or automatic downloads.
- Installation writes a temporary file, flushes/fsyncs it and atomically publishes it; duplicate target names are rejected. Runtime loader reconstructs data-only skill objects.
- These files are durable user configuration and belong in the storage manifest. They are not the same as executable plugin modules or the in-memory SkillRegistry.

**Gaps:** install/remove paths resolve and create their directory without the new private-profile validator; directory permissions, symlink races, concurrent install/remove, owner approval and provenance/retained license metadata need tests. The pack's optional `license` metadata is user-supplied text, not proof of license rights.

## Confirmed non-persistent runtime registries and caches

- `OIDCJWKSCache` is a bounded in-memory cache with TTL, refresh throttling and per-instance RLock. It must be repopulated after restart; no backup required.
- `PreparedOAuthProviderRegistry` is an immutable in-memory mapping of prepared authorization services and safe presentation metadata. Reconstruct from approved host configuration; it does not persist provider secrets.
- `PlatformRegistry` is an immutable in-memory capability snapshot. Credentials are opaque `secret://` references, not values.
- `SkillRegistry` is an in-memory registry of skill objects; installed skill-pack JSON files are the separate durable source.
- `defense/security/osint_audit.py` explicitly implements an immutable in-memory hash chain, not durable storage or an actor-signature system. Do not list it as a durable store or claim its hash chain proves actor identity.

## Manifest changes

The canonical storage manifest now includes:
- encrypted companion memory and its OS-keyring/profile-identity dependency;
- stable profile identity;
- installed data-only skill packs;
- explicit `VERIFIED_NOT_A_STORE` entries for the OIDC cache, OAuth provider registry, platform registry, runtime skill registry and in-memory OSINT audit tuple.

## Readiness

These additions improve inventory completeness but do not close the global maintenance or backup gaps. No writer is yet protected by an application-wide maintenance lease. Do not automatically migrate profiles, copy/restore key material, enable unattended skill-pack execution, or claim all memory implementations are encrypted. Keep desktop encrypted-memory behavior distinct from generic companion-memory defaults.
