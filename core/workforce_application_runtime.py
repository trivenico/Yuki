"""Shared composition root for Yuki's supervised AI workforce.

This runtime owns one durable task engine and composes the CEO planner, the
metadata-only employee registry, the guarded Yuki skill executor, and one
supervised worker. It does not start background threads or model instances.
Call worker.run_once() from an explicitly managed worker process.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.durable_task_engine import DurableTaskEngine
from core.planner import Planner
from core.workforce import AgentRegistry
from core.workforce_ceo import WorkforceCEO
from core.workforce_executor import YukiAgentWorkforceExecutor
from core.workforce_review import IndependentQualityReviewer
from core.workforce_worker import WorkforceWorker


@dataclass
class WorkforceApplicationRuntime:
    """Own the workforce composition and its durable task-store lifecycle."""

    engine: DurableTaskEngine
    registry: AgentRegistry
    ceo: WorkforceCEO
    executor: YukiAgentWorkforceExecutor
    worker: WorkforceWorker
    _closed: bool = False

    @property
    def closed(self) -> bool:
        return self._closed

    def run_once(self) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("workforce runtime is closed")
        return self.worker.run_once()

    def close(self) -> None:
        if self._closed:
            return
        self.engine.close()
        self._closed = True


def build_workforce_application_runtime(
    *,
    database: str | Path,
    agent: Any,
    registry: AgentRegistry | None = None,
    planner: Planner | None = None,
    reviewer: IndependentQualityReviewer | None = None,
    worker_id: str = "yuki-workforce-worker",
    max_autonomy_tier: int = 1,
    require_independent_review: bool = False,
    lease_seconds: int = 60,
    heartbeat_interval_seconds: float = 20,
) -> WorkforceApplicationRuntime:
    """Build one explicit workforce runtime around Yuki's existing skill gate.

    The database must be persistent; this production composition intentionally
    rejects SQLite's process-local :memory: mode. Authentication and task
    submission remain responsibilities of the calling application surface.
    """
    if not isinstance(database, (str, Path)) or not str(database).strip():
        raise ValueError("database must be a non-empty persistent path")
    if str(database) == ":memory:":
        raise ValueError("workforce runtime requires a persistent SQLite database")
    if not callable(getattr(agent, "run_skill", None)):
        raise TypeError("agent must provide the guarded run_skill() interface")
    if registry is None:
        registry = AgentRegistry()
    if not isinstance(registry, AgentRegistry):
        raise TypeError("registry must be an AgentRegistry")
    if planner is None:
        planner = Planner()
    if not isinstance(planner, Planner):
        raise TypeError("planner must be a Planner")
    if reviewer is not None and not isinstance(reviewer, IndependentQualityReviewer):
        raise TypeError("reviewer must be an IndependentQualityReviewer")

    engine = DurableTaskEngine(database)
    try:
        executor = YukiAgentWorkforceExecutor(agent)
        ceo = WorkforceCEO(
            planner=planner,
            registry=registry,
            task_engine=engine,
            max_autonomy_tier=max_autonomy_tier,
            require_independent_review=require_independent_review,
        )
        worker = WorkforceWorker(
            engine,
            registry,
            worker_id=worker_id,
            executor=executor,
            reviewer=reviewer,
            lease_seconds=lease_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        return WorkforceApplicationRuntime(
            engine=engine,
            registry=registry,
            ceo=ceo,
            executor=executor,
            worker=worker,
        )
    except Exception:
        engine.close()
        raise
