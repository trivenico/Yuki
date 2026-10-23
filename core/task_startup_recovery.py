"""Startup recovery report for durable agent task checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

from core.task_checkpoint_store import SQLiteTaskCheckpointStore


@dataclass(frozen=True)
class TaskRecoveryReport:
    total_incomplete: int
    stale_running: tuple[str, ...]
    needs_reconciliation: tuple[str, ...]
    paused: tuple[str, ...]
    pending: tuple[str, ...]
    running: tuple[str, ...]
    truncated: bool


def recover_task_checkpoints(
    workspace: str | Path, *, database_name: str = "task_checkpoints.sqlite3",
    stale_after_seconds: float = 300.0, limit: int = 1000,
) -> TaskRecoveryReport:
    """Classify interrupted tasks without replaying or guessing about side effects.

    A running checkpoint is changed to needs_reconciliation because a process
    may have exited after an external effect but before persisting its outcome.
    This is conservative classification, not proof that an effect occurred.
    """
    if database_name != "task_checkpoints.sqlite3":
        raise ValueError("Unsupported task checkpoint database name.")
    if type(stale_after_seconds) not in (int, float) or stale_after_seconds < 0:
        raise ValueError("stale_after_seconds must be non-negative.")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000.")
    root = Path(workspace).expanduser().resolve()
    db_path = root / database_name
    if db_path.is_symlink():
        raise RuntimeError("Task checkpoint database must not be a symlink.")
    if not db_path.exists():
        return TaskRecoveryReport(0, (), (), (), (), (), False)
    store = SQLiteTaskCheckpointStore(str(db_path))
    items = store.list_incomplete(limit=limit)
    stale_running: list[str] = []
    needs_reconciliation: list[str] = []
    paused: list[str] = []
    pending: list[str] = []
    running: list[str] = []
    now = datetime.now(timezone.utc)
    for item in items:
        if item.status == "running":
            try:
                updated = datetime.fromisoformat(item.updated_at)
                age = max(0.0, (now - updated).total_seconds())
            except (TypeError, ValueError):
                age = stale_after_seconds + 1
            if age >= stale_after_seconds:
                store.checkpoint(
                    item.task_id, expected_revision=item.revision,
                    payload=item.payload, status="needs_reconciliation",
                )
                stale_running.append(item.task_id)
                needs_reconciliation.append(item.task_id)
            else:
                running.append(item.task_id)
        elif item.status == "needs_reconciliation":
            needs_reconciliation.append(item.task_id)
        elif item.status == "paused":
            paused.append(item.task_id)
        elif item.status == "pending":
            pending.append(item.task_id)
    return TaskRecoveryReport(
        total_incomplete=len(items), stale_running=tuple(stale_running),
        needs_reconciliation=tuple(needs_reconciliation), paused=tuple(paused),
        pending=tuple(pending), running=tuple(running), truncated=len(items) == limit,
    )
