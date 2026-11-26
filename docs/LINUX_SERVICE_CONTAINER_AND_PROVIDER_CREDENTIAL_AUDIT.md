# Linux Service, Container, and Provider Credential Boundary Audit

**Review date:** 2026-11-26  
**Reviewed revision:** `03becc4f2c5eaea9667d1b985b393d4cb5e17d0f`  
**Status:** source review only; no host, container daemon, secrets, or deployment inspected

## Scope

Reviewed the Dockerfile and operator guidance, the example systemd host-inventory unit, the CLI entrypoint, provider catalog and OpenAI-compatible adapter, package metadata, and default configuration.

No Docker daemon was contacted. No systemd unit was installed or started. No process environment or provider credential value was read.

## Confirmed controls

- Docker image runs as non-root UID 10001 (`yuki`) and owns its application/workspace directories.
- Docker guidance explicitly warns that the image is not a sandbox and instructs operators not to mount host home directories, Docker socket, SSH keys, cloud credentials, or other secrets. It recommends dropping capabilities, read-only root filesystem, disposable workspace, and CPU/memory/network limits.
- The example systemd unit runs under a dedicated `yuki-host-agent` account, enables `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, kernel protections, an empty capability set, and `RestrictAddressFamilies=AF_UNIX`.
- The OpenAI-compatible adapter reads API credentials from a named environment variable, rejects untrusted cloud hosts and redirects, bounds request/response sizes and timeouts, and does not print credential values.
- CLI provider-test and ask are explicit user-invoked operations. Provider failures are reduced to exception types rather than raw exception text. The CLI persistent chat closes its conversation runtime in a finally block and explicitly supplies an empty tool list.
- GitHub Actions quality workflows declare `contents: read`; the inspected HF model scout is read-only discovery, uploads a bounded report artifact, and does not require a provider token.
- Default config keeps privileged integrations disabled, Python plugin discovery disabled, and MCP server list empty.

## Findings and gaps

### 1. Dockerfile is a convenience runtime, not a production isolation profile

The image runs non-root, but the Dockerfile itself does not enforce read-only root, dropped capabilities, seccomp/AppArmor policy, PID limits, memory/CPU limits, network egress policy, or a health check. These are runtime deployment properties and cannot be inferred from the Dockerfile. The README correctly says they must be applied by the container runtime.

There is no compose/production container deployment manifest in the reviewed tree. No production container policy or host secret-injection mechanism is established.

**Required:** keep the image classified as developer/guarded-terminal convenience. For a future private worker deployment, use a dedicated UID, read-only root, explicit writable tmpfs/workspace, no Docker socket, no host home or broad credential mounts, dropped capabilities, no-new-privileges, restrictive seccomp/AppArmor, PID/memory/CPU limits, default-deny egress with explicit destinations, bounded logs, and an explicit shutdown/health contract. Pass only task-scoped credentials through a secret broker or short-lived mounted credential file; never bake secrets into image layers or build args.

### 2. systemd example is narrowly scoped and not the Yuki application host

The only systemd unit found is `deploy/systemd/yuki-host-runtime-inventory.service.example`. Its environment file is `/etc/yuki/host-runtime-inventory.env`, but the source tree does not establish its ownership/mode, contents, or whether it contains secrets. `RestrictAddressFamilies=AF_UNIX` also means this service is not a general network-connected application service.

The unit's `ExecStart` names `core.host_runtime_inventory_service_cli`; that exact module path was not present in the recursive tree at the reviewed revision. Treat the example as potentially stale until the entrypoint is verified or corrected.

**Required:** verify the module exists and is packaged before calling this unit deployable. Document `root:root` ownership and `0600` mode for any secret-bearing EnvironmentFile, or use systemd credentials (`LoadCredential=` / `LoadCredentialEncrypted=`) rather than ambient environment variables. Keep host inventory agent credentials distinct from Yuki provider credentials. Do not add provider API keys to this unit.

### 3. Environment inheritance is not scoped in the application launcher

The cloud adapter obtains the configured API key from the current process environment. The CLI does not itself create a child process for provider calls, so the credential is available to that Yuki process. This is expected for the direct request, but no common credential broker or per-task environment projection is established for future worker subprocesses.

A broad parent environment can be inherited by any child process unless the launcher explicitly scrubs it. The Docker probe is a positive exception: it creates a temporary HOME and passes a minimal environment to the Docker CLI subprocess.

**Required:** centralize credential resolution behind a provider credential interface. For every child worker, construct an allow-listed environment rather than copying `os.environ`; never pass credentials to browser, shell, plugin, Kaggle, or untrusted task workers. Prefer short-lived, narrowly scoped secret handles and redact environment-derived values from logs and diagnostics.

### 4. Provider configuration supports credentials in YAML

`core/config.py` reads `database_url` from YAML as well as `YUKI_DATABASE_URL`. This means the configuration file may contain a password-bearing PostgreSQL URL. The prior configuration audit already identified missing validation of existing config-file and parent permissions. The provider API key itself is environment-based in the inspected OpenAI adapter.

**Required:** reject literal credential-bearing URLs in persisted YAML; accept a secret reference or host-injected credential only. Redact userinfo and query secrets from all diagnostics. Validate the config file and parent directory before reading.

### 5. Provider status is metadata, not proof of authentication

The provider catalog reports whether an environment variable appears configured. For OpenAI it does not validate that the key is valid; readiness means key-present-and-model-configured, not successful authentication. Gemini similarly reports CLI presence while explicitly noting that auth is not verified.

**Required:** preserve these distinctions in UI/CLI labels. A real provider connectivity test must be explicit, bounded, privacy-disclosed, and must never log or persist the probe response or secret.

### 6. CI and scheduled model scout separation

The inspected test workflow uses read-only repository permissions and does not inject production credentials. The scheduled Hugging Face scout reads public model metadata and uploads a JSON report. This is a useful separation, but the full set of workflows and repository/org-level secrets/permissions were not audited here.

**Required:** audit every workflow individually before allowing secrets. Use least-privilege token permissions, pin third-party actions to reviewed immutable commit SHAs, protect release environments, never expose secrets to fork PRs, and keep Kaggle training credentials separate from deployment credentials. Scheduled discovery must never auto-promote a model or deploy it.

## Canonical manifest additions

| Logical record | Owner / runtime | Secret boundary | Status |
|---|---|---|---|
| `runtime.docker.convenience` | Container runtime operator | No host secrets or Docker socket mounted | PARTIAL — non-root image; isolation controls are external |
| `runtime.systemd.host_inventory` | Dedicated host agent | EnvironmentFile mode/contents unverified; not provider credentials | PARTIAL — restrictive unit example; ExecStart module unresolved |
| `credentials.provider.openai` | Yuki provider process | `OPENAI_API_KEY` environment mapping | PARTIAL — adapter boundary exists; host injection and child-process scrubbing not centralized |
| `credentials.database.postgres` | Config/runtime | May currently be a password-bearing YAML URL or env value | BLOCKED — secret-safe config contract not implemented |
| `ci.github_actions` | GitHub-hosted workflow runners | Per-workflow token and optional secrets | PARTIAL — sampled workflows least privilege; complete workflow/secret audit outstanding |

## Readiness decision

Do not deploy the Docker image as a security boundary by itself. Do not use the host-inventory systemd example as the production Yuki service definition. Do not put provider or database credentials in YAML, image layers, Docker build arguments, task payloads, or broad inherited worker environments.

Before private Linux VPS deployment, create and review the actual Yuki application systemd unit, service account, private data/config roots, systemd credential injection, network policy, writable path list, process shutdown contract, and health/rollback checks. Keep the host-inventory agent separate.

This is a documentation-only audit. No runtime code, secrets, host configuration, container, workflow, or production data was changed.
