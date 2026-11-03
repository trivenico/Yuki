"""One-call construction of the existing provider-backed catalog worker.

This is dependency wiring only: it does not start a polling loop, claim tasks,
or grant coordinator/runtime permissions.
"""
from __future__ import annotations

from collections.abc import Mapping

from automation.specialist_provider_wiring import build_catalog_provider_handlers
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime


def create_catalog_specialist_worker(
    runtime: TaskRuntime,
    *,
    worker_id: str,
    service_actor_id: str,
    provider_manager,
    role_map: Mapping[str, str],
    provider_preferences: Mapping[str, tuple[str, ...]] | None = None,
    allow_cloud: bool = False,
) -> SpecialistTaskWorker:
    """Construct a worker whose handler IDs match catalog specialist IDs.

    The application owns lifecycle, scheduling, actor authorization, and
    coordinator registration. Provider preferences and cloud use are explicit.
    """
    if not isinstance(runtime, TaskRuntime):
        raise TypeError("runtime must be a TaskRuntime.")
    handlers = build_catalog_provider_handlers(
        provider_manager,
        role_map=role_map,
        provider_preferences=provider_preferences,
        allow_cloud=allow_cloud,
    )
    return SpecialistTaskWorker(
        runtime,
        worker_id=worker_id,
        service_actor_id=service_actor_id,
        handlers=handlers,
    )
