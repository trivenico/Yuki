"""Shared composition root for Yuki's supervised AI workforce.

This runtime owns one durable task engine and composes the CEO planner, the
metadata-only employee registry, the guarded Yuki skill executor, and one
supervised worker. It does not start background threads or model instances.
Call worker.run_once() from an explicitly managed worker process.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any
import threading
import time
import math

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
    _stopping: bool = False
    _active_runs: int = 0
    _lifecycle: threading.Condition = dataclass_field(default_factory=threading.Condition, repr=False)

    @property
    def closed(self) -> bool:
        return self._closed

    def run_once(self) -> dict[str, Any]:
        with self._lifecycle:
            if self._closed:
                raise RuntimeError("workforce runtime is closed")
            if self._stopping:
                return {"state": "stopping", "status": "shutdown_requested"}
            self._active_runs += 1
        try:
            return self.worker.run_once()
        finally:
            with self._lifecycle:
                self._active_runs -= 1
                self._lifecycle.notify_all()

    def shutdown(self, timeout_seconds: float = 30.0) -> bool:
        """Stop new runs and wait up to a bounded deadline for active work.

        False means the deadline elapsed: the engine intentionally remains open
        so active execution can keep heartbeating its lease.
        """
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
            or timeout_seconds > 3600
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 3600")
        with self._lifecycle:
            if self._closed:
                return True
            if not self._stopping:
                self._stopping = True
                self.worker.request_stop()
            deadline = time.monotonic() + float(timeout_seconds)
            while self._active_runs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._lifecycle.wait(remaining)
            return True

    def close(self) -> None:
        if self._closed:
            return
        if not self.shutdown(timeout_seconds=0):
            raise RuntimeError(
                "cannot close workforce runtime while task execution is active; "
                "request shutdown and wait for active work to drain"
            )
        with self._lifecycle:
            if self._closed:
                return
            self._closed = True
        self.engine.close()


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
