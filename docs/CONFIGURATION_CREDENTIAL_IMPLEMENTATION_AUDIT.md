# Configuration and Credential Boundary: Implementation Audit

**Review date:** 2026-11-26  
**Reviewed branch:** `research/reference-projects-integration-assessment`  
**Status:** source review only; no secret values read, no local tests run, no host or deployment changes

## Scope and verified implementation

Reviewed `core/config.py`, `core/portable_paths.py`, `providers/factory.py`, `providers/openai_compatible.py`, `yuki_cli.py`, and `pyproject.toml`.

- `load_config()` resolves a config path, reads YAML with `Path.read_text()`, then validates the mapping and settings.
- The wheel first-run path creates a missing config with exclusive creation and mode 0600, fsyncs the file and parent on POSIX, and avoids overwriting a concurrently created file.
- Existing config reads do not first validate file type, ownership, mode, hard-link count, parent-directory permissions, or symlink ancestry.
- `config_path()` resolves explicit `YUKI_CONFIG` and default config paths with `.resolve()`. This follows symlinks before a security-sensitive reader could reject them.
- `YUKI_HOME` preserves lexical path components in `data_root()`, but this does not secure config paths selected by `YUKI_CONFIG`.
- PostgreSQL configuration currently accepts `database_url` from YAML or `YUKI_DATABASE_URL`. The parsed URL requires username and password and enforces TLS for non-loopback hosts. Thus a password-bearing DSN can be persisted in YAML.
- The returned config dictionary includes the full `database_url`. Any downstream diagnostic/logging code that serializes the whole config could expose credentials unless it explicitly redacts them.
- `providers.factory.create_provider()` resolves OpenAI API keys through `OPENAI_API_KEY` (or the explicitly selected hybrid key variable) in the process environment. It does not read a systemd credential file.
- `OpenAICompatibleProvider` validates HTTPS endpoint trust, rejects redirects, and bounds requests/responses; this is an HTTP endpoint boundary, not a secret-delivery or child-process isolation mechanism.
- `yuki_cli.py` builds providers in the CLI process. Provider status distinguishes key-present from verified connectivity; explicit provider-test sends a fixed probe and does not print the response.
- `pyproject.toml` exposes the CLI console script and does not currently define a production ASGI server entrypoint.

## Security findings

### CRED-01 — Existing config files are trusted before validation

The first-run file creation is restrictive, but later reads do not verify that an existing config is a regular file owned by the expected OS identity, has restrictive permissions, has no unexpected hard links, or resides beneath a private directory chain. A custom `YUKI_CONFIG` path may point to a shared or attacker-controlled location. Path resolution can hide symlink components from a later validator.

**Risk:** another local principal may alter configuration or substitute a file; secrets stored in config may be disclosed to principals with directory/file access.

**Required:** preserve the lexical path; validate every existing path component without following symlinks; open the final file with no-follow semantics and validate the opened descriptor with `fstat`; enforce regular-file, owner, mode, link-count, and bounded-size policy. POSIX and Windows need separate validators. Windows must fail closed until DACL ownership/access validation is implemented. Do not chmod or repair an existing unsafe file silently.

### CRED-02 — Password-bearing PostgreSQL DSNs are accepted from YAML

The loader explicitly reads `database_url` from the YAML mapping and requires URL userinfo. This contradicts the intended non-secret configuration contract.

**Risk:** database credentials may be copied into backups, support bundles, source-control mistakes, diagnostics, crash reports, or shared config directories.

**Required:** reject non-empty YAML `database_url` with a clear migration error. Accept a non-secret credential reference (for example a systemd credential file identifier) and resolve the secret only at the storage-adapter boundary. Keep compatibility only through an explicit one-time operator migration; never silently migrate a plaintext DSN or print it.

### CRED-03 — Secret resolution is coupled to ambient process environment

Provider credentials are obtained from process environment variables. There is no shared credential-provider abstraction or systemd credential-file adapter in the inspected composition path.

**Risk:** credentials are inherited by child processes and can be exposed through process inspection, crash dumps, diagnostic bundles, or accidental environment forwarding. Environment variables also make secret rotation and per-worker scoping harder.

**Required:** introduce a narrow credential source contract with explicit providers:
- OS credential store for interactive desktop;
- systemd credential directory for the Linux service;
- explicit environment adapter only for development/CI compatibility, with warnings and a migration path.

Resolve a secret only immediately before constructing the provider that needs it. Do not put secret values in the general config dictionary, task payloads, logs, exceptions, CLI arguments, or persisted state.

### CRED-04 — Child process environment projection is not a platform contract

The inspected provider factory has no shared worker environment allowlist or credential stripping contract. Existing isolated subprocess examples are narrow exceptions, not a system-wide guarantee.

**Risk:** future browser, shell, plugin, model, or task workers may inherit Yuki's provider/database credentials.

**Required:** a single child-process launcher must require an explicit environment mapping and never default to `os.environ.copy()`. Default-deny all secret variables. Add tests that inject sentinel credentials and prove they are absent from worker environments, argv, task serialization, logs, and error messages.

### CRED-05 — Config path and secret rotation semantics are undefined

The loader returns a snapshot of configuration values. No coordinated reload/rotation contract was established in this review.

**Risk:** replacing credentials while requests are in flight can leave stale provider clients alive; partially updated configuration can mix credential and endpoint revisions.

**Required:** represent configuration as a versioned immutable snapshot. Rotate by constructing and validating a new client, atomically switching new work to it, draining the old client, and closing it. Never mutate a shared provider client in place. Failed rotation must retain the last known-good client and emit a sanitized audit event.

## Proposed implementation contract

### Non-secret settings

YAML may contain provider name/model, timeouts, storage backend, pool sizes, endpoint identifiers, feature toggles, and credential *references*. It must not contain:
- provider API key values;
- PostgreSQL passwords or credential-bearing DSNs;
- OAuth client secrets or refresh tokens;
- encryption keys or signing keys;
- systemd credential contents.

### Linux systemd credential delivery

- Unit uses `LoadCredential=provider-openai:/etc/yuki/credentials/provider-openai` (or encrypted credential source where supported) and a separate credential for database authentication.
- Provisioning is an operator action with root-owned source files and restrictive permissions; Yuki never reads the source path directly.
- At runtime, systemd exposes read-only credential copies under `$CREDENTIALS_DIRECTORY`. The application validates the expected filename, opens with no-follow semantics, bounds the size, reads once, keeps the value in memory only as long as required, and never logs it.
- Do not put secret contents in `Environment=`, `EnvironmentFile=`, `ExecStart=`, unit drop-ins, command arguments, or YAML.
- The application environment contains only the credential directory path and non-secret settings.
- Provider and database credentials are separate files with separate rotation and access policies.

### Interactive desktop and CLI

- Desktop continues to use the OS keyring-backed credential path.
- Headless CLI needs an explicit policy: owner OS account is the local trust boundary, or require a Yuki owner session. Until selected and implemented, do not claim CLI provider operations are independently authenticated by the Yuki account.
- CLI provider test and ask remain explicit external-data disclosure operations. Persistent chat keeps tool execution deny-all until a separately reviewed authorization path exists.
- Linux CLI and service must share the same private profile and credential abstractions without sharing ambient process environments.

## Safe migration plan

1. Add platform-specific secure config reader and tests; preserve lexical path and use descriptor-bound checks.
2. Add secret-reference schema and credential source interface without changing existing provider behavior.
3. Add systemd credential adapter and desktop keyring adapter; unit-test with temporary credential directories and fake keyring.
4. Reject password-bearing YAML DSNs. Provide an explicit dry-run migration report that identifies the key name/path only, never prints the value. Require operator approval to migrate; do not automatically rewrite config.
5. Update provider construction to accept a secret handle/value only at the provider boundary, not in the general config object.
6. Add a child-process launcher with explicit environment allowlists and sentinel-secret leak tests.
7. Add rotation lifecycle and tests for failed replacement, concurrent requests, drain, and rollback.
8. Only then write the actual Linux application systemd unit and validate it in a disposable systemd-capable environment.

## Readiness

The current implementation has useful first-run file creation, provider endpoint validation, redirect rejection, bounded provider I/O, and sanitized CLI error messages. It does not yet provide a secure existing-config read boundary, a non-secret PostgreSQL configuration contract, systemd credential injection, a central secret provider, or a guaranteed child-process environment allowlist.

**Decision:** keep production Linux service deployment blocked. Do not add a service unit or claim production credential isolation until CRED-01 through CRED-05 are implemented and CI covers their threat cases.

This document records source-level findings and a proposed contract only. No secrets were read, migrated, rotated, or changed.
