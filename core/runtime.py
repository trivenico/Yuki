"""Bounded asynchronous supervisor for Yuki's persistent queued-task runtime.

This module intentionally composes only registered handlers and the guarded
orchestrator. It does not discover tools, grant permissions, or auto-run repairs.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from core.job_queue import SQLiteJobQueue
from core.queued_orchestrator import Authorizer, Handler, QueuedTaskOrchestrator

logger = logging.getLogger(__name__)


class RuntimeState(str, Enum):
    NEW = "new"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class RuntimeSettings:
    worker_count: int = 2
    idle_poll_seconds: float = 0.25
    lease_seconds: int = 120
    shutdown_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if type(self.worker_count) is not int or not 1 <= self.worker_count <= 32:
            raise ValueError("worker_count must be an integer from 1 to 32")
        if isinstance(self.idle_poll_seconds, bool) or not 0.01 <= self.idle_poll_seconds <= 30:
            raise ValueError("idle_poll_seconds must be between 0.01 and 30")
        if type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600")
        if isinstance(self.shutdown_timeout_seconds, bool) or not 0.1 <= self.shutdown_timeout_seconds <= 300:
            raise ValueError("shutdown_timeout_seconds must be between 0.1 and 300")


class YukiRuntime:
    """Own worker tasks and provide explicit start/stop semantics.

    Handlers are synchronous because the queue orchestrator contract is synchronous.
    They run via ``asyncio.to_thread`` so SQLite/handler I/O does not block the event
    loop. A handler must still enforce its own network/process deadlines: cancelling
    an asyncio task cannot forcibly stop Python code already running in a thread.
    """

    def __init__(
        self,
        queue: SQLiteJobQueue,
        handlers: Mapping[str, Handler],
        authorizer: Authorizer,
        *,
        worker_id_prefix: str = "yuki-worker",
        settings: RuntimeSettings = RuntimeSettings(),
        on_worker_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if not isinstance(worker_id_prefix, str) or not worker_id_prefix.strip() or len(worker_id_prefix) > 80:
            raise ValueError("worker_id_prefix must be non-empty and at most 80 characters")
        self.queue = queue
        self.handlers = dict(handlers)
        self.authorizer = authorizer
        self.worker_id_prefix = worker_id_prefix.strip()
        self.settings = settings
        self.on_worker_error = on_worker_error
        self.state = RuntimeState.NEW
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._orchestrators = [
            QueuedTaskOrchestrator(
                queue, self.handlers, authorizer,
                worker_id=f"{self.worker_id_prefix}-{index + 1}",
                lease_seconds=settings.lease_seconds,
            )
            for index in range(settings.worker_count)
        ]

    async def start(self) -> None:
        """Start the fixed-size worker pool once; safe to call only from an event loop."""
        if self.state is not RuntimeState.NEW:
            raise RuntimeError(f"Cannot start runtime from state {self.state.value}.")
        self._stop_event = asyncio.Event()
        self.state = RuntimeState.RUNNING
        try:
            self._tasks = [
                asyncio.create_task(self._worker_loop(orchestrator), name=orchestrator.worker_id)
                for orchestrator in self._orchestrators
            ]
        except Exception:
            self.state = RuntimeState.FAILED
            self._stop_event.set()
            raise

    async def _worker_loop(self, orchestrator: QueuedTaskOrchestrator) -> None:
        while not self._stop_event.is_set():
            try:
                job = await asyncio.to_thread(orchestrator.run_one)
                if job is None:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=self.settings.idle_poll_seconds)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Yuki worker loop encountered an internal error")
                if self.on_worker_error is not None:
                    try:
                        self.on_worker_error(orchestrator.worker_id, exc)
                    except Exception:
                        logger.exception("Yuki worker error callback failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.settings.idle_poll_seconds)
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        """Stop claiming work, allow in-flight calls a grace period, then cancel tasks."""
        if self.state is RuntimeState.STOPPED:
            return
        if self.state is RuntimeState.NEW:
            self.state = RuntimeState.STOPPED
            return
        if self.state is RuntimeState.FAILED:
            self._stop_event.set()
        else:
            self.state = RuntimeState.STOPPING
            self._stop_event.set()
        if self._tasks:
            done, pending = await asyncio.wait(
                self._tasks, timeout=self.settings.shutdown_timeout_seconds,
                return_when=asyncio.ALL_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self.state = RuntimeState.STOPPED

    async def serve(self) -> None:
        """Wait until all workers unexpectedly exit, then mark the runtime failed."""
        if self.state is not RuntimeState.RUNNING:
            raise RuntimeError("Runtime must be started before serve().")
        try:
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            if not self._stop_event.is_set() and any(isinstance(item, BaseException) for item in results):
                self.state = RuntimeState.FAILED
        finally:
            if self.state is RuntimeState.RUNNING:
                self.state = RuntimeState.FAILED

    async def __aenter__(self) -> "YukiRuntime":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.stop()
