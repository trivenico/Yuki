# Phase 274 — Verification workspace targeting and startup audit

## Delivered

Extended `build_local_worker_runtime` with an optional
`verification_workspace` parameter. Durable task, budget, artifact, acceptance,
and execution-receipt databases remain under the configured Yuki state workspace,
while the controlled verification executor can run in a separately materialized
engineering workspace.

This is a prerequisite for executing tests against a pinned source snapshot
without relocating Yuki's persistent state.

Added a local-runtime test that configures a separate execution workspace,
runs an explicitly allow-listed interpreter version command, and checks that
the executor uses the requested directory. The test uses `sys.executable`
for Windows/Linux/macOS portability.

## Startup audit findings

The current `main.py` creates the local worker runtime without
`verification_commands`, so `verification_executor` remains `None` during
ordinary startup. This is deliberate fail-closed behavior, but it means
verification execution is not yet available through the default terminal
startup path.

The Git-backed workspace manager is also not yet instantiated by `main.py`.
No claim is made that `python main.py` currently exposes the complete
reproducible engineering workflow.

## Safety and integration boundaries

- Verification remains disabled unless trusted application code supplies an
  explicit command allowlist.
- The workspace path must already exist.
- The executor still runs with the current OS user's permissions; this is not
  an OS sandbox.
- The sample test invokes only the current Python interpreter's version
  command. It does not execute a repository test suite.
- Main startup wiring, operator-facing controls, sandbox isolation, and
  end-to-end snapshot-to-receipt validation remain future work.

## Verification status

Changes and tests are committed. Tests have not been run in this environment.
No CI pass or production-readiness claim is made.
