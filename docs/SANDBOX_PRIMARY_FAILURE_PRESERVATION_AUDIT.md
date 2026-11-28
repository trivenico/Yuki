# Sandbox primary-failure preservation audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: cleanup failure replaces the primary execution exception

In `core/coding_task_runner.py::run_sandboxed_task`, execution and timeout handling occur inside a `try` whose `finally` block calls `_kill_container(container_name)`. If cleanup returns false, the `finally` block raises a new `SandboxExecutionError`.

Python propagates that new exception in preference to an exception already being unwound from the execution block. For example, if `process.wait(timeout=5)` raises because the Docker CLI does not stop after timeout and container cleanup also cannot be confirmed, the caller receives only the cleanup error; the original timeout/shutdown failure is lost. The same masking can occur for other exceptions raised while waiting for the process.

This does not make cleanup optional: failure to confirm cleanup must remain visible. The defect is loss of the primary failure and the absence of a structured representation of both outcomes.

## Impact

Operators and callers cannot reliably distinguish the original execution failure from the additional cleanup failure. This can impair incident diagnosis, retry decisions, and accurate reporting of whether the task timed out, the Docker client stopped, and container removal was confirmed.

## Required remediation

1. Capture the primary execution exception rather than allowing a cleanup exception in `finally` to replace it.
2. Represent execution and cleanup outcomes together in a typed error/result, or use exception chaining / an exception group where supported.
3. Preserve fail-closed behavior: never return a normal successful result when cleanup is unconfirmed.
4. Add tests for simultaneous primary wait failure and cleanup failure, asserting that both causes remain inspectable; also cover timeout escalation plus cleanup failure.

No runtime code changed, no Docker daemon was contacted, no container was created or removed, and no local tests were run.
