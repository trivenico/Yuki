"""Explicit composition root for the optional background runtime.

Only the harmless built-in health task is registered by default. Application
features must register reviewed handlers and an authorizer explicitly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import load_config
from core.job_queue import SQLiteJobQueue
from core.runtime_service import RuntimeService


def build_runtime(config: dict[str, Any], *, max_workers: int = 2) -> RuntimeService:
    workspace = Path(config["workspace"]).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_dir = workspace / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    queue = SQLiteJobQueue(runtime_dir / "jobs.sqlite3")

    def health_handler(payload: dict[str, Any]) -> dict[str, Any]:
        component = payload.get("component", "runtime")
        if component != "runtime":
            raise ValueError("Only runtime health is supported by this built-in handler.")
        return {"component": "runtime", "healthy": True, "version": 1}

    def authorize(task_type: str, payload: dict[str, Any]) -> bool:
        # This default composition root intentionally exposes no arbitrary tasks.
        return task_type == "system.health" and payload.get("component", "runtime") == "runtime"

    return RuntimeService(
        queue,
        {"system.health": health_handler},
        authorize,
        max_workers=max_workers,
        lease_seconds=30,
    )


def build_runtime_from_config(*, max_workers: int = 2) -> RuntimeService:
    return build_runtime(load_config(), max_workers=max_workers)
