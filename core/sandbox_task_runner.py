"""Orchestrate approved tasks through durable lifecycle and sandbox launcher."""
from __future__ import annotations
from typing import Any, Callable, Sequence
from core.bounded_execution import ExecutionError, run_bounded
from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import SandboxRuntimeError, build_docker_argv
from core.task_lifecycle import SQLiteTaskLifecycleStore, TaskRecord

class SandboxTaskError(RuntimeError):
    """A task could not safely proceed through the sandbox lifecycle."""

def execute_sandbox_task(store: SQLiteTaskLifecycleStore, *, idempotency_key: str,
    task_type: str, payload: dict[str, Any], policy: SandboxPolicy, image: str,
    workspace: str, command: Sequence[str], authorize: Callable[[dict[str, Any]], bool],
    env: dict[str, str]) -> TaskRecord:
    """Execute a pending task only after independent approval; never auto-replay.

    A timeout is treated as an uncertain side effect and transitions to recovery_required.
    The caller must pass a deliberate environment allowlist. This function requires a
    working, correctly configured Docker daemon; it does not prove host isolation.
    """
    if not callable(authorize):
        raise SandboxTaskError("an independent authorization callback is required")
    if not isinstance(payload, dict):
        raise SandboxTaskError("payload must be an object")
    if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                        or "\x00" in k + v for k, v in env.items()):
        raise SandboxTaskError("env must be an explicit string mapping")
    task = store.create_task(idempotency_key=idempotency_key, task_type=task_type, payload=payload)
    if task.state in {"completed", "failed", "cancelled", "recovery_required", "running", "approved"}:
        return task
    if task.state != "pending":
        raise SandboxTaskError("task is not in a runnable initial state")
    try:
        allowed = authorize({"task_id": task.task_id, "task_type": task.task_type,
            "payload": task.payload, "command": list(command), "policy": policy})
    except Exception:
        return store.transition(task.task_id, expected_state="pending", new_state="failed",
            expected_version=task.version, error_code="authorization_error")
    if allowed is not True:
        return store.transition(task.task_id, expected_state="pending", new_state="failed",
            expected_version=task.version, error_code="approval_denied")
    approved = store.transition(task.task_id, expected_state="pending", new_state="approved",
        expected_version=task.version)
    running = store.transition(task.task_id, expected_state="approved", new_state="running",
        expected_version=approved.version)
    try:
        argv = build_docker_argv(policy, image=image, workspace=workspace, command=command)
        result = run_bounded(argv, timeout_seconds=policy.timeout_seconds,
            max_output_bytes=policy.max_output_bytes, env=env)
    except (SandboxRuntimeError, ExecutionError, OSError):
        return store.transition(running.task_id, expected_state="running", new_state="failed",
            expected_version=running.version, error_code="sandbox_launch_failed")
    if result.timed_out:
        return store.transition(running.task_id, expected_state="running", new_state="recovery_required",
            expected_version=running.version, error_code="execution_timeout_uncertain")
    if result.returncode != 0:
        return store.transition(running.task_id, expected_state="running", new_state="failed",
            expected_version=running.version, error_code="sandbox_nonzero_exit")
    return store.transition(running.task_id, expected_state="running", new_state="completed",
        expected_version=running.version, result={"returncode": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "duration_seconds": result.duration_seconds, "output_truncated": result.output_truncated})
