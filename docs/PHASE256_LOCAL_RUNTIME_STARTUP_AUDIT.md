# Phase 256 — Local integration test correction and startup audit

## Correctness fix

The coordinator-to-provider integration test now reads the task's persisted `definition` field. `TaskRecord` exposes `definition`; it does not expose a `steps` attribute. The earlier test draft would therefore fail before reaching the provider assertions. This correction makes the test exercise the intended path rather than relying on an invalid test fixture API.

## Runtime wiring confirmed

- `main.py` constructs `build_local_worker_runtime(workspace, ai.provider)` during startup.
- The local factory shares the task runtime, coordinator, worker supervisor, and durable SQLite budget store.
- The dashboard receives those same objects and supports task/plan creation, approval, cancellation, and explicit worker cycles.
- Worker execution is operator-triggered; the factory creates no scheduler or background worker threads.
- The dashboard is optional and requires the separate UI dependencies.

## Important operational distinction

`python main.py` starts Yuki's main companion runtime and prepares the local specialist runtime. To operate specialist plans, the user opens `/dashboard`; specialist work remains approval-gated and is executed through explicit worker cycles. This is not yet a one-command unattended engineering service.

## Verification status

The test API correction is committed. No Python test suite has been executed in this environment. The GitHub connector available here can inspect commit status and selected workflow metadata, but its workflow-run lookup is limited to pull-request-triggered runs; an empty result does not establish that CI passed or failed.

## Next release gate

- Execute the complete unittest suite on the repository branch.
- Fix failures before adding more integration layers.
- Add a deterministic local smoke command that validates provider readiness, database creation, coordinator submission, approval and one worker cycle without requiring a live cloud provider.
- Document clean installation for Windows, macOS and Linux, including optional dashboard dependencies.
