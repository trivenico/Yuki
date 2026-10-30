"""Composition root for running the persisted specialist-agent fleet."""
from __future__ import annotations

from automation.agent_handler_factory import build_agent_handlers
from automation.specialist_worker import SpecialistTaskWorker
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from automation.task_runtime import TaskRuntime


def build_registered_agent_worker(
    runtime: TaskRuntime,
    registry: SQLiteAgentRegistry,
    provider_manager,
    *,
    worker_id: str,
    service_actor_id: str,
) -> SpecialistTaskWorker:
    """Build a worker whose handler set is derived from the durable agent registry.

    This is the supported composition path for the starter fleet. It reuses
    Yuki's existing worker and provider router, and refuses split SQLite state.
    """
    if not isinstance(runtime, TaskRuntime):
        raise TypeError("runtime must be a TaskRuntime.")
    if not isinstance(registry, SQLiteAgentRegistry):
        raise TypeError("registry must be a SQLiteAgentRegistry.")
    if runtime.store.database_path != registry.database_path:
        raise ValueError("agent registry and task runtime must share the same SQLite database.")
    handlers = build_agent_handlers(provider_manager, registry)
    if not handlers:
        raise ValueError("no registered specialist agents are available.")
    return SpecialistTaskWorker(
        runtime,
        worker_id=worker_id,
        service_actor_id=service_actor_id,
        handlers=handlers,
    )
