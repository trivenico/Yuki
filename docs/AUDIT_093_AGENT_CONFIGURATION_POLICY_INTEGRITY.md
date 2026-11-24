# Audit 093 — Agent Configuration Security, Policy Drift, and Runtime Integrity

Repository: trivenico/Yuki  
Base: main  
Scope: configuration loader/defaults, environment overrides, provider and database settings, plugin trust configuration, permission/autonomy primitives, application runtime composition.  
Outcome: source audit plus one focused security fix submitted separately.

## Executive summary

Yuki's configuration loader has several strong foundations: safe YAML parsing, bounded numeric/list settings, explicit boolean parsing, safe Ollama transport validation, fail-closed feature defaults, private permissions for newly created first-run configuration, and validation of storage/provider choices.

The review found a concrete transport-authentication weakness: remote PostgreSQL configuration accepted sslmode=require and sslmode=verify-ca. Those modes encrypt traffic but do not guarantee hostname verification. Duplicate sslmode parameters were also collapsed into a dictionary, making the effective value dependent on parser/client handling. A focused fix now requires exactly one sslmode=verify-full for remote PostgreSQL and preserves loopback development behavior.

Other findings concern configuration governance rather than a demonstrated exploit: privileged feature flags can be changed through environment overrides; plugin wildcard trust remains accepted by the configuration path; loaded configuration is a plain mutable dictionary without a policy revision or runtime drift contract; and existing config-file permissions are not checked when loading an already-present file.

## Files inspected

- core/config.py
- core/default_config.yaml
- core/plugin_loader.py
- core/permissions.py
- core/tiered_autonomy.py
- core/agent_execution_loop.py
- automation/agent_coordinator.py
- api/runtime.py
- pyproject.toml
- tests/test_provider_config_loading.py
- tests/test_conversation_store_factory.py
- tests/test_provider_setup_config_precedence.py
- tests/test_mcp_runtime_wiring.py
- tests/test_optional_modules.py

## Positive controls observed

1. YAML is parsed with safe_load and the top-level value must be a mapping.
2. Boolean environment overrides accept only explicit true/false spellings; malformed values fail closed.
3. Numeric settings have explicit bounds and booleans are rejected where integers are expected.
4. Provider, storage backend, TTS backend, model lengths, MCP server counts, allow-list lengths, and pool sizes are validated.
5. Ollama allows HTTPS or loopback HTTP only and rejects URL credentials, query, and fragment.
6. Default configuration disables voice, wakeword, Gmail, security lab, sandbox, firewall, and plugin discovery.
7. First-run config creation uses exclusive creation, fsync, and owner-only mode on POSIX.
8. PermissionGate requires an explicit approver and accepts only literal True; callback exceptions fail closed.
9. TieredAutonomyPolicy never overrides per-action approval for state-changing or external actions.
10. Runtime API composition validates injected service types and derives API owner identity from authenticated sessions.

## Findings

### P1 — Remote PostgreSQL configuration did not require hostname verification

Before the submitted fix, core/config.py accepted remote PostgreSQL sslmode=require, verify-ca, or verify-full. require encrypts but does not authenticate the server hostname; verify-ca validates the CA chain but does not validate the requested hostname. The loader also converted query options to a dictionary, collapsing duplicate sslmode keys.

Risk: a remote database client could establish an encrypted connection to a server presenting a certificate from a trusted CA but for the wrong hostname, weakening protection against misdirection or interception.

Remediation submitted in implementation PR #1162:
- Require exactly one sslmode=verify-full query parameter for remote PostgreSQL.
- Reject weaker, missing, and duplicate sslmode parameters.
- Reject URL fragments.
- Preserve local loopback development behavior.
- Add regression tests for weak modes, duplicate modes, valid verify-full, and loopback.

The tests were added but not executed. No live PostgreSQL TLS integration test was performed.

### P1 — Privileged feature configuration has no explicit production policy profile

Environment variables can override feature flags including sandbox, security lab, Gmail, firewall, plugin discovery, and voice. This is useful for deployment automation, but the inspected loader has no explicit deployment profile that distinguishes development from production or prevents a deployment layer from enabling privileged functionality without an accompanying reviewed policy bundle.

Risk: operational configuration drift can enable features that were disabled in the checked-in default template. The loader validates boolean syntax, not whether the deployment is authorized to enable the feature or whether its required dependencies, approvals, and isolation controls are present.

Required direction:
- Introduce explicit environment profiles and a centrally validated feature policy.
- Require feature-specific prerequisites (for example, sandbox runtime attestation and plugin artifact trust) before activation.
- Record effective configuration provenance and policy revision at startup.
- Do not treat environment variables as an authorization mechanism; privileged feature activation must be governed by deployment policy and runtime permission gates.

### P1 — Wildcard plugin trust remains representable in effective configuration

trusted_plugin_modules is validated as a list of non-empty strings, but the loader accepts the wildcard * for every discoverable module. The plugin loader then imports module code into the host process. The default remains disabled, which is good, but the configuration contract does not prevent broad wildcard trust when plugin discovery is enabled.

Risk: adding a module to the package can silently expand the executable code trust boundary in deployments that enable wildcard discovery.

Required direction: production config validation should reject wildcard trust, require exact module IDs and artifact digests, and bind each module to signed provenance. This must be paired with isolated plugin execution; an exact module name alone is not a sandbox.

### P2 — Existing configuration file permissions are not verified on read

First-run creation sets a newly created POSIX config file to mode 0600. When a configuration file already exists, load_config reads it without checking whether its permissions are still private. The config can contain a PostgreSQL URL with username/password and integration settings.

Risk: a manually created, copied, restored, or permission-modified config file may be readable by other local users even though first-run creation is private.

Required direction:
- On POSIX, inspect the existing file with lstat/fstat semantics and reject symlinks or group/world-readable modes for secret-bearing config.
- Avoid silently chmod'ing shared or externally managed files unless an explicit repair workflow is chosen.
- Document Windows ACL expectations and add platform-specific permission tests.

### P2 — Effective configuration has no immutable snapshot or policy revision

load_config returns a plain dictionary. The inspected runtime composition receives config and builds services, but no immutable canonical snapshot, config digest, policy revision, or source provenance is attached to the runtime for audit and drift detection.

Risk: an operator cannot reliably reconstruct which exact configuration and feature policy governed a task after restart or incident. Components may also retain different snapshots if configuration is reloaded independently.

Required direction:
- Parse into a typed immutable settings object.
- Canonicalize and hash non-secret effective settings; never include secret values in the digest report.
- Attach config revision and provenance to runtime startup/audit events.
- Define explicit reload semantics: restart-only, or atomic validated policy update with component acknowledgement.
- Never expose secret-bearing settings in logs or status endpoints.

### P2 — Configuration validation and runtime enforcement are separate contracts

The loader validates settings, but individual components also accept constructor parameters and may be instantiated outside the standard application composition. The reviewed runtime does not establish one shared immutable feature/policy object passed to every privileged component.

Risk: tests, alternate entry points, or future specialist agents may construct a privileged subsystem with settings that bypass the central configuration path.

Required direction: require typed policy dependencies at privileged constructors and enforce authorization/isolation at the resource boundary. Configuration is desired state, not an authorization decision.

## Recommended implementation order

1. Land and execute PR #1162's PostgreSQL TLS regression tests.
2. Add secure existing-config permission validation and symlink handling.
3. Define immutable effective settings with redacted provenance and policy revision.
4. Add production profile rules for privileged feature activation.
5. Reject wildcard plugin trust in production and require signed artifact identity.
6. Make privileged components consume central policy contracts and revalidate at action time.
7. Add tests for config tampering, env override precedence, feature prerequisites, policy drift, and alternate runtime construction.

## Validation and limitations

This audit is based on source inspection of the listed paths. The implementation PR contains regression tests but they were not executed. No live PostgreSQL TLS handshake, malicious config file, multi-user filesystem, production deployment, or runtime policy-drift scenario was tested.

PR #1162 is an implementation proposal and remains separate from this audit document. Neither PR has been merged or deployed.
