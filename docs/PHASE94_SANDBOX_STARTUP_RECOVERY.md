# Phase 94 — Sandbox recovery startup gate

## Integration point

`api.runtime.build_conversation_runtime` accepts an optional, explicitly constructed `sandbox_job_recovery` coordinator. The coordinator is not silently created from guessed Docker settings: production composition must provide the real durable store, trusted jobs root, digest-pinned Docker runtime, and mandatory audit callback.

## Startup contract

During ASGI lifespan startup, before sending `lifespan.startup.complete`, the runtime calls `recover_nonterminal()`.

- Outcomes such as `removed`, `missing`, and safe `deferred` states allow startup to continue.
- Any `recovery_required` outcome blocks startup.
- Exceptions, malformed results, Docker daemon errors, audit failures, or durable-store failures also block startup.
- On failure, the runtime sends `lifespan.startup.failed`, closes owned application resources, and does not report startup completion.
- The ASGI server therefore must not begin serving requests after an unsuccessful startup event.

## Operational requirements

The production composition root must construct `SandboxJobStore` and `DockerJobRuntime` from operator-controlled settings and pass a `SandboxJobRecovery` instance into `build_conversation_runtime`. The Docker image must be digest-pinned and the command must be fixed host-owned argv. Do not derive Docker command arguments from user or agent input.

If no coordinator is passed, this integration remains inactive for compatibility. Production deployments that enable sandbox jobs must treat the coordinator as mandatory and verify that it is injected. This change does not auto-enable Docker or create a runtime from incomplete configuration.

## Verification status

Runtime tests cover successful recovery before startup completion, ambiguous recovery blocking startup, and invalid dependency rejection. These tests have been added but not independently executed in this development environment. No merge or deployment has occurred.
