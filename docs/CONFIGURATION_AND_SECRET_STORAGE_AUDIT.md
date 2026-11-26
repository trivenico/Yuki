# Configuration and Secret-Storage Boundary Audit

**Review date:** 2026-11-26  
**Branch:** `research/reference-projects-integration-assessment`  
**Reviewed revision:** `d75c29fabca037c87230d959e4b243358266df59`  
**Status:** source review only; no runtime configuration or secrets inspected

## Scope

Reviewed `config.yaml`, `.env.example`, `core/config.py`, `core/skill_packs.py`, `core/oauth_credential_vault.py`, `core/oauth_os_key_provider.py`, and `providers/model_registry.py`.

This audit does not read any deployed config, environment variables, keyring entries, OAuth ciphertext, or user data. It describes code paths and configuration semantics only.

## Confirmed controls

- `.env.example` explicitly says the application does not auto-load `.env`; it warns against committing real credentials and recommends setting provider credentials in the shell or a secret manager.
- Default configuration disables Python plugin discovery and privileged/integration features. The plugin loader correctly warns that imported Python modules execute with Yuki's privileges and are not sandboxed.
- First-run wheel configuration creation uses exclusive file creation, requests mode `0600`, fsyncs the file and parent directory on POSIX, and does not overwrite a concurrently created config.
- PostgreSQL URLs are validated for scheme/host/credentials and remote PostgreSQL requires TLS.
- OAuth credential ciphertext is stored separately from key material. The OAuth key provider explicitly selects native Windows Credential Locker, macOS Keychain, or Linux Secret Service backends; it does not silently fall back to environment variables or plaintext files.
- The OAuth vault uses versioned credential records and compare-and-swap refresh generations. Key identifiers are stored with encrypted values; HMAC key material is host-managed separately.
- Installed skill packs are JSON data, not imported Python modules. Their imported instruction content is marked high risk and approval-required by the skill-pack integration path.
- Model registry writes use a temporary file and atomic replace.

## Gaps and required decisions

### 1. Existing configuration file permissions are not verified on read

The first-run path creates a private config file, but `load_config()` subsequently reads the selected existing file directly. The reviewed code does not establish that an existing config file or every parent directory is owner-controlled and private before reading it.

This matters because `database_url` can be supplied in YAML as well as through `YUKI_DATABASE_URL`. A PostgreSQL URL may contain a username and password. Other future secret-bearing settings could also be added to this file.

**Required:** define one canonical config root; validate owner, file type, symlink policy, parent chain, and restrictive permissions before reading an existing config. Fail closed on unsupported Windows ACL verification until a real DACL validator exists. Keep secrets out of YAML by policy and support secret references or host secret-manager injection for credentials. Redact database URLs and secret references from diagnostics and error messages.

### 2. Explicit config paths need the same trust boundary

The loader accepts a caller-supplied path. The source review does not establish a separate safe policy for custom paths, symlinked parents, network shares, or shared project directories.

**Required:** custom config paths must pass the same no-follow, ownership, ACL and permission checks as the canonical path, or be explicitly classified as non-secret configuration that cannot contain credentials. Do not silently treat a project workspace as a private secret store.

### 3. Environment variables are references, not durable secret storage

The example uses `OPENAI_API_KEY` in the launch environment and `YUKI_DATABASE_URL` for PostgreSQL. Environment variables can be exposed through process inspection, crash diagnostics, service managers, or child-process inheritance depending on the host and launch configuration.

**Required:** document the owner-operated Linux service secret-injection method (prefer a secret manager or systemd credentials over broad environment inheritance), restrict process inspection and service account access, prevent secrets from entering CLI arguments, shell history, logs, crash reports, task payloads, and child-worker environments. Pass narrowly scoped credentials only to the process that needs them.

### 4. Skill-pack directory and path validation

The skill-pack helper resolves the configured/default directory before use and creates directories with `mkdir(parents=True, exist_ok=True)`. Install publication is temp-file plus atomic replace/no-overwrite, but this does not itself prove that the directory is private, owner-controlled, or free from symlink/path races. Removal resolves the target path before unlinking.

**Required:** use the canonical private-profile directory validator and no-follow directory-descriptor operations for install/list/remove; validate ownership and permissions; bind leaf operations to opened directory handles; preserve source URL, exact revision, license, content digest, imported timestamp, and approval state. Keep instruction data non-executable and continue to require explicit approval before execution.

### 5. Model registry concurrent writes

The model registry writes via temp file and atomic replace, but the inspected save path does not acquire the shared cross-process JSON file lock used by other stores. Atomic replacement prevents partial-file publication but does not prevent lost updates from concurrent read/modify/write operations.

**Required:** wrap the entire read/modify/write transaction in the shared cross-process lock, validate schema/version, and use a private directory. Add independent-process concurrency tests.

### 6. OAuth key recovery is a separate backup dependency

The OAuth vault database alone is not a complete backup. Ciphertext depends on OS-managed AEAD and HMAC key material. Restoring the database without the matching key identifiers and recoverable keys can make credentials unusable; silently generating replacement keys over existing ciphertext is explicitly unsafe.

**Required:** define an owner-approved key recovery/export/import ceremony, key version inventory, rotation and revocation procedure, recovery test, and restore ordering. Never place raw key material in the canonical storage manifest or ordinary backup logs. If a platform keyring cannot securely recover the required keys, report the vault as non-restorable rather than silently degrading.

## Canonical manifest additions

Add these logical records to the storage manifest:

| Store ID | Backend | Classification | Key dependencies | Status |
|---|---|---|---|---|
| `config.owner` | YAML configuration file | Mixed; may currently contain database credentials | Secret references and deployment identity | PARTIAL — existing file/parent permissions and custom-path validation not established |
| `secrets.provider_runtime` | Host-injected environment/secret manager | Credential | Provider-specific secret source | PARTIAL — example documents environment injection; production host method unverified |
| `oauth.os_keyring` | Windows Credential Locker / macOS Keychain / Linux Secret Service | Credential/key material | Native OS user profile and keyring unlock/recovery | PARTIAL — backend selection is explicit; disaster-recovery procedure unverified |
| `skills.installed_packs` | JSON instruction files | Untrusted imported content | Provenance, source revision and license metadata | PARTIAL — atomic publication exists; private directory/path boundary needs hardening |
| `registry.model` | JSON model metadata | Internal model configuration | Model artifact revisions and provenance | PARTIAL — cross-process write lock missing in inspected save path |

## Readiness decision

Do not treat the current configuration directory as a proven secret vault. Do not place production provider/database credentials in project YAML, workspace files, CLI arguments, or task payloads. Do not enable arbitrary Python plugin discovery. Keep OAuth key recovery explicitly unresolved until a tested owner-controlled recovery procedure exists.

This is a source-code audit only. No code behavior, user data, credentials, keyring state, deployment, or production service was changed.
