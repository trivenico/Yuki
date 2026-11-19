# Phase 95 — durable sandbox production composition

## Scope

This phase adds a production composition path for the durable Docker job
recovery coordinator. It does not enable sandbox execution by default, expose
job lifecycle endpoints, approve artifact promotion, or create a Docker daemon.

## Configuration

The separate `sandbox_jobs_enabled` setting defaults to `false`. It is
independent of the legacy interactive `sandbox_enabled` switch.

When enabled, configure all of the following in the operator-owned YAML config:

- `sandbox_jobs_root`: an existing directory owned by the Yuki service UID,
  with mode `0700`.
- `sandbox_jobs_image`: an immutable image reference pinned to a lowercase
  `sha256` digest.
- `sandbox_jobs_command`: a non-empty, host-owned argv list. It is never
  assembled from a user prompt, task argument, or model output.
- `sandbox_jobs_docker_binary`: Docker executable name/path; defaults to
  `docker`.

The configured workspace must already exist. The composition factory refuses
missing paths, symlinks, unsafe root ownership/mode, an unavailable Docker
executable, malformed configuration, or an unpinned image. It does not create
or chmod the jobs root.

The factory creates two SQLite metadata databases inside the private jobs
root: `sandbox-jobs.sqlite3` and `sandbox-recovery-audit.sqlite3`. Database
files are restricted to mode `0600`. Recovery audit actions include the job
identifier in a bounded fixed-schema action field; no user prompt or arbitrary
exception text is persisted.

## Required production wiring

Deployments enabling durable jobs must construct the API runtime through
`api.production_runtime.build_production_conversation_runtime`, passing the
same config and normal runtime dependencies. Do not call
`api.runtime.build_conversation_runtime` directly for a durable-jobs-enabled
deployment: that lower-level builder intentionally remains available for
tests and explicit dependency injection.

The production factory creates the SQLite store, Docker runtime, recovery
audit adapter, and `SandboxJobRecovery` as one composition, then injects the
coordinator into the existing ASGI runtime. The Phase 94 lifespan hook runs
recovery before startup completes. Any `recovery_required` result, malformed
outcome, Docker failure, audit failure, or store failure blocks startup.

When durable jobs are disabled, the factory returns the normal runtime without
initializing Docker or either sandbox database.

## Explicit limitations

- The checked-in ASGI launcher is `scripts/run_web.py`. Phase 96 wires it
  through the production factory. Existing deployments must update to this
  branch and use that launcher to receive mandatory durable-job recovery
  composition; the separate `main.py` interactive CLI remains a different
  runtime surface.
- This phase composes recovery only. It does not wire a public or model-callable
  job submission API, a promotion approval UI, or an artifact promotion
  executor. Those require a separately reviewed authenticated service and
  explicit approval path.
- Docker daemon availability is exercised when recovery needs to reconcile a
  persisted launching/running job. If the daemon is unavailable in that case,
  startup fails closed.
- No sandbox job execution is enabled by the default configuration.

## Verification status

Regression tests cover disabled-mode non-initialization, incomplete enabled
configuration, private database permissions, production-factory dependency
injection, and rejection of caller-supplied recovery coordinators. The tests
were added but have not been independently executed in this environment.
