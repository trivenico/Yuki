# Local worker approval-session shutdown audit

**Review date:** 2026-11-28  
**Scope:** `main.py`, `automation/local_worker_runtime.py`, `automation/workflow_store.py`, `automation/task_runtime.py`, and `ui/dashboard.py`.

## Finding: normal application exit does not revoke the local worker approval session

The local worker runtime creates a fresh random `approval_session_id` for each process and persists task approvals against a hash of that session. `LocalWorkerRuntime.shutdown()` explicitly writes a durable revocation marker for that session before draining the supervisor. The workflow store's `claim_next()` and `is_execution_authorized()` reject approvals whose session hash appears in the durable revocation table.

However, the current application composition does not consistently invoke this shutdown contract:

- `main.py::main()` constructs `local_workers = build_local_worker_runtime(...)` during startup.
- The CLI's normal `/quit`, EOF, and KeyboardInterrupt paths return from `main()` without a `finally` block that calls `local_workers.shutdown()`.
- The optional Textual dashboard receives the runtime/supervisor and exposes `/quit` via `self.exit()`, but `run_dashboard()` has no unmount/exit hook that retires the runtime's approval session. Returning from the dashboard also leaves the local runtime object in the enclosing CLI process.

The workflow database is stored under the same private workspace and reused on subsequent launches. Because claim-time authorization checks revocation and expiry, but does not require the approval session to equal the new process's session, an earlier approval can remain eligible for a later worker cycle until its approval expiry if the old session was never revoked.

This is a lifecycle authorization defect: approval intended to be scoped to an ephemeral local operator session can survive the normal exit of that session. It is not a claim that a remote attacker can forge approvals or that a worker runs automatically in the background. A later explicit worker cycle is required, and the approval must still be within its configured expiry.

## Evidence and positive controls

- `automation/local_worker_runtime.py` creates a random session ID and documents that shutdown retires it before draining.
- `LocalWorkerRuntime.shutdown()` calls `runtime.revoke_session_approvals(self.approval_session_id)` before `supervisor.shutdown()`.
- `tests/test_local_worker_runtime.py` explicitly tests that shutdown writes the revocation marker and that revocation occurs before drain.
- `automation/workflow_store.py` filters revoked session hashes in `claim_next()` and rechecks the same condition in `is_execution_authorized()`.
- `main.py` has no call to `local_workers.shutdown()` in its normal exit paths.
- `ui/dashboard.py` implements `/quit` by calling `self.exit()` and has no `on_unmount` cleanup for the supplied supervisor/runtime.

## Required remediation

1. Give the application composition root sole ownership of the local runtime lifecycle and call its shutdown in a guaranteed cleanup path for normal exit, EOF, KeyboardInterrupt, dashboard exit, and startup failure after runtime construction.
2. Ensure dashboard exit retires the same runtime session before control returns to the CLI; avoid double-shutdown races by making lifecycle ownership explicit and shutdown idempotent.
3. If shutdown cannot drain active synchronous provider calls, keep dependencies alive and surface an incomplete-shutdown state; do not silently close the provider/runtime beneath active work.
4. Add integration tests that approve a task, exit the dashboard or CLI through each supported path, then construct a new runtime over the same database and prove the prior approval cannot be claimed.
5. Add failure-injection tests for revocation persistence failure and supervisor drain timeout. Fail closed and preserve a retry handle.

## Validation limits

Source inspection only. No runtime code changed, no local tests run, and no worker cycle or provider request executed. The finding is supported by the application composition and the existing store's revocation checks; no deployed exploit or unattended execution path is asserted.
