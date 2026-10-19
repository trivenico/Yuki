"""Lifecycle wrapper for Yuki's durable async job scheduler.

The daemon does not execute arbitrary command strings. Applications must register each
handler explicitly and supply an approval callback before starting the worker.
"""
from __future__ import annotations
import asyncio
import logging
import signal
from pathlib import Path
from typing import Any, Callable
from automation.async_scheduler import AsyncJobScheduler

logger = logging.getLogger("yuki.daemon")

class YukiDaemon:
    def __init__(self, database: str | Path, *, approval_required,
                 poll_seconds: float = 1.0):
        self.scheduler = AsyncJobScheduler(database, approval_required=approval_required,
                                            poll_seconds=poll_seconds)
        self._task: asyncio.Task | None = None
        self._closed = False

    def register_job(self, name: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        if self._task is not None:
            raise RuntimeError("Register job handlers before starting the daemon.")
        self.scheduler.register_handler(name, handler)

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Daemon has been closed.")
        if self._task is not None:
            return
        self._task = asyncio.create_task(self.scheduler.run_forever(), name="yuki-scheduler")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.scheduler.stop()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

async def serve_until_signal(daemon: YukiDaemon) -> None:
    """Run until SIGINT/SIGTERM where supported, then drain and close."""
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    await daemon.start()
    try:
        await stop.wait()
    finally:
        await daemon.close()
