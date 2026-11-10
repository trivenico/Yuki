"""Supervised polling loop and CLI entry point for Yuki's durable worker.

The service never approves tasks automatically. Tasks requiring approval remain
queued until an authenticated application control explicitly approves them.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from yuki_agent_runtime import TaskStore
from yuki_agent_worker import AgentWorker, WorkerOutcome
from yuki_provider_worker import create_provider_handlers
from yuki_providers import ProviderManager


LOGGER = logging.getLogger("yuki.worker")


def run_worker_loop(
    worker: AgentWorker,
    stop_event: threading.Event,
    *,
    idle_sleep_seconds: float = 1.0,
    on_outcome: Callable[[WorkerOutcome], None] | None = None,
) -> None:
    """Poll until shutdown, sleeping only while idle and never auto-approving."""
    if isinstance(idle_sleep_seconds, bool) or not isinstance(idle_sleep_seconds, (int, float)):
        raise TypeError("idle_sleep_seconds must be a number")
    if not 0.05 <= idle_sleep_seconds <= 60:
        raise ValueError("idle_sleep_seconds must be between 0.05 and 60 seconds")
    while not stop_event.is_set():
        outcome = worker.run_once()
        if on_outcome is not None:
            on_outcome(outcome)
        if outcome.status == "idle":
            stop_event.wait(idle_sleep_seconds)


def _build_worker(environ=None) -> AgentWorker:
    env = os.environ if environ is None else environ
    provider_name = str(env.get("YUKI_PROVIDER", "mock")).strip().lower()
    database = str(env.get("YUKI_TASK_DB", "~/.yuki/agent_tasks.sqlite3")).strip()
    worker_id = str(env.get("YUKI_WORKER_ID", f"yuki-worker-{os.getpid()}")).strip()
    if not database:
        raise ValueError("YUKI_TASK_DB must be a non-empty path")
    if not worker_id:
        raise ValueError("YUKI_WORKER_ID must be non-empty")

    config = {"provider": provider_name}
    if env.get("YUKI_OLLAMA_MODEL"):
        config["ollama_model"] = env["YUKI_OLLAMA_MODEL"]
    manager = ProviderManager(config=config, environ=env)
    selected = manager.select(provider_name)
    if not selected.startswith("Selected provider:"):
        raise RuntimeError(f"Cannot configure provider '{provider_name}'.")
    handlers = create_provider_handlers(manager)
    store = TaskStore(Path(database).expanduser())
    return AgentWorker(store, worker_id=worker_id, handlers=handlers)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Yuki's bounded durable task worker.")
    parser.add_argument("--once", action="store_true", help="claim and execute at most one task, then exit")
    parser.add_argument("--poll-interval", type=float, default=1.0,
                        help="idle polling interval in seconds (0.05 to 60)")
    args = parser.parse_args(argv)
    if not 0.05 <= args.poll_interval <= 60:
        parser.error("--poll-interval must be between 0.05 and 60 seconds")

    try:
        worker = _build_worker()
    except Exception as exc:
        LOGGER.error("Worker configuration failed (%s).", type(exc).__name__)
        return 2

    if args.once:
        outcome = worker.run_once()
        LOGGER.info("Worker outcome: status=%s task_id=%s error_type=%s",
                    outcome.status, outcome.task_id, outcome.error_type)
        return 0 if outcome.status in {"idle", "succeeded", "cancelled"} else 1

    stop_event = threading.Event()

    def request_shutdown(signum, _frame):
        LOGGER.info("Received shutdown signal %s; stopping after current handler returns.", signum)
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, request_shutdown)
        except (ValueError, OSError):
            # Some embedded/test runtimes do not allow signal registration.
            pass

    def report(outcome):
        LOGGER.info("Worker outcome: status=%s task_id=%s error_type=%s",
                    outcome.status, outcome.task_id, outcome.error_type)

    try:
        run_worker_loop(worker, stop_event, idle_sleep_seconds=args.poll_interval, on_outcome=report)
    except Exception as exc:
        LOGGER.error("Worker loop stopped unexpectedly (%s).", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("YUKI_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(main())
