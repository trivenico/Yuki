# Private Linux Application Service: Entrypoint and Lifecycle Gate

**Review date:** 2026-11-26  
**Reviewed branch:** `research/reference-projects-integration-assessment`  
**Status:** source review only; no local tests, host inspection, service installation, or deployment

## Decision

Do not write or install a production Yuki systemd application unit yet. The reviewed branch exposes reusable application components and a CLI, but the inspected package metadata and source do not establish a packaged, runnable, network-serving application entrypoint with a complete process lifecycle.

A systemd unit written now would either invent an entrypoint or incorrectly repurpose a different process. Keep the host inventory agent separate from the Yuki application service.

## Entrypoints verified

- `pyproject.toml` installs the `yuki` console script as `yuki_cli:main`.
- The CLI supports diagnostics, provider setup/status/test, one-shot provider requests, persistent chat, and local owner bootstrap. It is an interactive/operator interface, not a daemon contract.
- `core/local_cli_runtime.py` builds persistent chat with an empty tool registry and deny-all authorizer. It does not provide a service listener.
- `api/runtime.py` provides a composable ASGI `ConversationRuntime`. Its ASGI lifespan handler closes the shared conversation application on shutdown. Construction requires injected auth and agent-loop services and may require other injected persistent services.
- `api/web.py` is an ASGI web shell/API delegate, not a server launcher. It exposes `/healthz` as a static `{"status":"ok","service":"yuki"}` response; this confirms only that the web wrapper answered, not that providers, databases, workers, or dependent stores are healthy.
- The inspected `pyproject.toml` does not declare Uvicorn, Hypercorn, or another ASGI server dependency, and declares no Yuki application-server console script.
- The only inspected systemd unit is `deploy/systemd/yuki-host-runtime-inventory.service.example`. It runs the host inventory agent, restricts address families to AF_UNIX, and references `core.host_runtime_inventory_service_cli`, which was not found by the repository search. Do not treat it as the Yuki application service.

## Consequences for deployment

1. **No guessed ExecStart.** Do not create a service unit that calls `yuki chat`, a speculative `yuki serve`, or an unverified Python module.
2. **Compose the application explicitly.** A future server entrypoint must construct provider adapters, auth, the agent loop, API runtime, and every enabled persistent service from a single reviewed composition root. Optional integrations remain disabled unless explicitly configured.
3. **Own every resource.** The current API runtime closes its shared conversation application only. Other injected services have independent ownership. The future top-level process must own and close every DB pool/store, worker, scheduler, provider client, and notification dispatcher in a deterministic order.
4. **Make shutdown bounded and observable.** Stop accepting new work; stop scheduler claims; signal workers; wait for in-flight tasks up to explicit deadlines; fence/release leases safely; close provider clients and all stores; emit a sanitized final audit event. A timeout must not silently declare success while a worker may still be performing side effects.
5. **Separate liveness from readiness.** Liveness should answer whether the process/event loop is alive. Readiness must check required configuration, storage connectivity/schema, audit writer/checkpoint dependencies, and required worker/scheduler state without leaking secrets. The existing static `/healthz` is not sufficient as a production readiness probe.
6. **Bind privately by default.** The initial owner-only Linux pilot should listen only on a private interface or loopback behind an authenticated private access path. No public listener, anonymous route, public signup, or unauthenticated health details.
7. **Use systemd credentials, not ambient secrets.** Prefer `LoadCredential=` or `LoadCredentialEncrypted=` for provider/database secrets. The process should read credential files from systemd's credential directory, validate permissions, and avoid copying secrets into YAML, command arguments, service environment dumps, or child environments.
8. **Use an explicit environment allowlist.** Pass only required non-secret settings to child processes. Browser, shell, plugin, Kaggle, and untrusted task workers receive no provider/database credentials or ambient host environment.
9. **Constrain the service identity.** Dedicated unprivileged OS user/group; `UMask=0077`; private config/data roots; read-only application code; only explicitly enumerated writable paths; no home-directory access; no capabilities; no-new-privileges; private tmp; kernel protections; strict resource limits; outbound network allowlist enforced outside application URL validation.
10. **Release only reviewed artifacts.** CI may build and test immutable artifacts. Deployment must require a protected release approval, artifact digest/provenance verification, backup/restore readiness, health checks, and rollback to the prior exact artifact. No deploy on every commit.

## Required implementation sequence

### Gate A — application composition

- Add a single production composition root with typed settings and explicit ownership of every injected service.
- Define a supported ASGI server dependency/version and stable console entrypoint only after composition is complete.
- Add startup failure behavior for missing required security dependencies; no permissive fallback.
- Add tests for partial-construction cleanup and idempotent shutdown.

### Gate B — lifecycle and health

- Add top-level lifespan management for every persistent store, pool, worker, scheduler, provider client, and external side-effect dispatcher.
- Define worker stop/join deadlines, in-flight task policy, lease fencing, and forced-shutdown recovery.
- Replace static health semantics with separate liveness and dependency-aware readiness endpoints.
- Add tests for SIGTERM-equivalent graceful shutdown, worker timeout, failed DB close, restart recovery, and readiness transitions.

### Gate C — configuration and credentials

- Validate config file and every relevant parent directory before reading; reject unsafe symlinks/ownership/permissions according to platform-specific validators.
- Remove password-bearing database URLs from persisted YAML; accept host-injected credential references or credential files.
- Implement a narrow secret-provider abstraction and systemd credential-file adapter.
- Scrub child-process environments and test that provider/database secrets never appear in argv, logs, diagnostics, task payloads, or worker environments.

### Gate D — deployable Linux service

- Add the real application systemd unit only after Gates A–C are implemented and CI validates its ExecStart/package path.
- Document service account creation, exact writable paths, private bind address, network policy, secret provisioning, backup dependencies, upgrades, rollback, and operator recovery.
- Validate the unit in a disposable Linux VM/container with systemd semantics; source inspection alone is not deployment proof.

## Explicit non-claims

- No production Yuki ASGI server entrypoint was verified in this review.
- No production service unit was created or installed.
- No systemd process, worker, scheduler, database, provider, or notification dispatcher was started.
- No credentials, user data, host configuration, or cloud resources were accessed.
- No tests were run locally; CI status must be checked independently.
