"""Long-running OCI/container entrypoint for Yuki's persisted specialist fleet.

Cloud routing requires an explicit per-agent deployment consent allowlist.
"""
from __future__ import annotations

import os
import signal
from dataclasses import dataclass

from automation.agent_runtime import build_registered_agent_worker
from automation.default_agents import seed_starter_agents
from automation.specialist_worker import AlwaysOnWorkerSupervisor
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from yuki_providers import ProviderManager

_WORKER_ACTIONS = frozenset({"task:complete", "task:fail", "task:recover"})


class RecoveringSpecialistWorker:
    """Recover expired leases before each claim; never automatically replay them."""

    def __init__(self, worker, runtime: TaskRuntime, service_actor_id: str):
        self.worker = worker
        self.runtime = runtime
        self.service_actor_id = service_actor_id

    def run_once(self):
        self.runtime.recover_expired(actor_id=self.service_actor_id, limit=50)
        return self.worker.run_once()


@dataclass
class WorkerService:
    runtime: TaskRuntime
    worker: object
    supervisor: AlwaysOnWorkerSupervisor


def build_worker_service(*, database_path: str, provider_manager=None,
                         worker_id: str = "yuki-specialist-worker",
                         service_actor_id: str = "yuki-worker-service") -> WorkerService:
    """Compose a worker over the gateway's exact database and explicit provider."""
    if not isinstance(database_path, str) or not database_path.strip():
        raise ValueError("database_path must be a non-empty path.")
    if provider_manager is None:
        selected = os.environ.get("YUKI_PROVIDER", "").strip().lower()
        if not selected:
            raise RuntimeError("Set YUKI_PROVIDER explicitly before starting the worker.")
        provider_manager = ProviderManager({"provider": selected})

    store = WorkflowRunStore(database_path)
    registry = SQLiteAgentRegistry(database_path)
    seed_starter_agents(registry)

    def authorize(actor: str, action: str, _task_id: str) -> bool:
        return actor == service_actor_id and action in _WORKER_ACTIONS

    runtime = TaskRuntime(store, authorize=authorize)
    raw_consent = os.environ.get("YUKI_WORKER_CLOUD_CONSENT_AGENT_IDS", "")
    cloud_consent_ids = tuple(item.strip() for item in raw_consent.split(",") if item.strip())
    worker = build_registered_agent_worker(
        runtime, registry, provider_manager,
        worker_id=worker_id, service_actor_id=service_actor_id,
        cloud_consent_agent_ids=cloud_consent_ids,
    )
    recovering = RecoveringSpecialistWorker(worker, runtime, service_actor_id)
    supervisor = AlwaysOnWorkerSupervisor(
        recovering,
        idle_poll_seconds=float(os.environ.get("YUKI_WORKER_IDLE_POLL_SECONDS", "1")),
        error_backoff_seconds=float(os.environ.get("YUKI_WORKER_ERROR_BACKOFF_SECONDS", "5")),
    )
    return WorkerService(runtime=runtime, worker=worker, supervisor=supervisor)


def main() -> None:
    database_path = os.environ.get("YUKI_GATEWAY_DATABASE", "/var/lib/yuki/gateway.sqlite3")
    worker_id = os.environ.get("YUKI_WORKER_ID", "yuki-specialist-worker")
    actor_id = os.environ.get("YUKI_WORKER_SERVICE_ACTOR_ID", "yuki-worker-service")
    service = build_worker_service(
        database_path=database_path, worker_id=worker_id, service_actor_id=actor_id,
    )

    def stop(_signum, _frame):
        service.supervisor.request_stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    service.supervisor.run_forever()


if __name__ == "__main__":
    main()
