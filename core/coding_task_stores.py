"""Canonical composition for coding-task plans and execution evidence.

Both SQLite stores share the same owner-private profile and maintenance-fence
authority. This coordinates maintenance only; it does not make their separate
database transactions atomic.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.coding_tasks import CodingTaskStore
from core.coding_task_execution import CodingTaskExecutionStore
from core.maintenance_fence import MaintenanceFence
from core.private_profile import ensure_private_profile_directory


@dataclass(frozen=True)
class CodingTaskStores:
    tasks: CodingTaskStore
    evidence: CodingTaskExecutionStore
    maintenance_fence: MaintenanceFence
    profile_root: Path


def build_coding_task_stores(
    profile_root: str | Path,
    *,
    maintenance_timeout_seconds: float = 10.0,
) -> CodingTaskStores:
    """Create both coding-task stores beneath one validated private profile."""
    root = ensure_private_profile_directory(profile_root)
    fence = MaintenanceFence(root, timeout_seconds=maintenance_timeout_seconds)
    tasks = None
    evidence = None
    try:
        tasks = CodingTaskStore(str(root / "coding-tasks.sqlite3"), maintenance_fence=fence)
        evidence = CodingTaskExecutionStore(
            str(root / "coding-task-evidence.sqlite3"), maintenance_fence=fence,
        )
    except Exception:
        for resource in (evidence, tasks):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        raise
    return CodingTaskStores(tasks, evidence, fence, root)
