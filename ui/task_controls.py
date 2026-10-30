"""Explicitly authorized operator controls for bounded worker execution."""

from __future__ import annotations

from collections.abc import Callable


def execute_worker_cycle(
    supervisor,
    *,
    actor_id: str,
    authorize: Callable[[str, str], bool],
    confirmation: str,
) -> str:
    """Run one bounded supervisor cycle after an explicit, authorized confirmation.

    The caller must supply the application identity/permission decision. This
    helper never creates a supervisor, worker, provider, or background service.
    """
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
        raise ValueError("a bounded operator identity is required.")
    if not callable(authorize):
        raise TypeError("an explicit authorization callback is required.")
    if confirmation != "RUN":
        raise ValueError("worker execution requires the exact RUN confirmation.")
    try:
        allowed = authorize(actor_id.strip(), "worker:run")
    except Exception:
        allowed = False
    if allowed is not True:
        raise PermissionError("worker execution is not authorized.")
    run_cycle = getattr(supervisor, "run_cycle", None)
    if not callable(run_cycle):
        raise TypeError("a configured WorkerSupervisor is required.")

    report = run_cycle()
    fields = (
        report.recovered_task_ids,
        report.processed_count,
        report.succeeded_count,
        report.failed_count,
        report.idle,
    )
    recovered, processed, succeeded, failed, idle = fields
    if type(processed) is not int or processed < 0:
        raise RuntimeError("worker supervisor returned an invalid report.")
    if type(succeeded) is not int or succeeded < 0 or type(failed) is not int or failed < 0:
        raise RuntimeError("worker supervisor returned an invalid report.")
    if not isinstance(recovered, tuple) or any(not isinstance(item, str) for item in recovered):
        raise RuntimeError("worker supervisor returned an invalid report.")
    if type(idle) is not bool or succeeded + failed > processed:
        raise RuntimeError("worker supervisor returned an invalid report.")
    return (
        "Worker cycle finished: "
        f"processed={processed}, succeeded={succeeded}, failed={failed}, "
        f"recovered={len(recovered)}, idle={str(idle).lower()}."
    )


def approve_queued_task(
    runtime,
    *,
    actor_id: str,
    session_id: str,
    authorize: Callable[[str, str], bool],
    task_id: str,
    expected_version: int,
    confirmation: str,
) -> str:
    """Approve one exact queued task definition for this local operator session."""
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
        raise ValueError("a bounded operator identity is required.")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
        raise ValueError("a valid operator session is required.")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
        raise ValueError("a bounded task identifier is required.")
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("task version must be a non-negative integer.")
    if confirmation != "APPROVE":
        raise ValueError("task approval requires the exact APPROVE confirmation.")
    if not callable(authorize):
        raise TypeError("an explicit authorization callback is required.")
    try:
        allowed = authorize(actor_id.strip(), "task:approve")
    except Exception:
        allowed = False
    if allowed is not True:
        raise PermissionError("task approval is not authorized.")
    store = getattr(runtime, "store", None)
    approve = getattr(store, "approve", None)
    if not callable(approve):
        raise TypeError("a configured task runtime is required.")
    run = approve(
        task_id.strip(), actor_id=actor_id.strip(),
        session_id=session_id, expected_version=expected_version,
    )
    return f"Approved task {run.run_id} at version {run.version} for this session."
