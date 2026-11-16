"""Run the opt-in sandbox executor as a separately supervised process.

The web/API process only queues work. This command owns the polling loop and
must be managed by a process supervisor (systemd, container orchestrator, etc.).
It finishes an in-flight task before honoring a graceful stop request.
"""
from __future__ import annotations

import logging
import os
import signal
import secrets
import threading
from pathlib import Path
from typing import Any

from automation.sandbox_execution_runtime import (
    SandboxExecutionRuntime,
    build_sandbox_execution_runtime,
)
from core.auth import BuiltInAuthService
from core.config import load_config

_LOG = logging.getLogger("yuki.sandbox_worker")
LEASE_SECONDS = 180


def _poll_interval(config: dict[str, Any]) -> float:
    value = config.get("sandbox_execution_poll_interval_seconds", 1.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("sandbox_execution_poll_interval_seconds must be numeric")
    if not 0.1 <= value <= 30:
        raise ValueError("sandbox_execution_poll_interval_seconds must be between 0.1 and 30")
    return float(value)


def run_worker(
    runtime: SandboxExecutionRuntime,
    *,
    stop_event: threading.Event,
    poll_interval: float = 1.0,
) -> None:
    """Poll one durable queue serially; stop only between task executions."""
    if not isinstance(runtime, SandboxExecutionRuntime):
        raise TypeError("runtime must be a SandboxExecutionRuntime")
    if not isinstance(stop_event, threading.Event):
        raise TypeError("stop_event must be a threading.Event")
    if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)):
        raise ValueError("poll_interval must be numeric")
    if not 0.1 <= poll_interval <= 30:
        raise ValueError("poll_interval must be between 0.1 and 30 seconds")

    # Execution tasks have max_attempts=1. Expired in-flight work is therefore
    # terminally failed rather than replayed: an external side effect may have
    # happened before the previous worker lost its lease.
    worker_id = runtime.worker.worker_id
    runtime.engine.register_worker(worker_id, os.getpid())
    heartbeat_stop = threading.Event()
    heartbeat_interval = min(max(float(poll_interval), 1.0), 10.0)

    def maintain_heartbeat():
        while not heartbeat_stop.wait(heartbeat_interval):
            try:
                runtime.engine.heartbeat_worker(worker_id)
            except Exception:
                # Never log exception text: database errors may contain paths.
                _LOG.error("Sandbox worker heartbeat update failed")

    heartbeat_thread = threading.Thread(
        target=maintain_heartbeat, name="yuki-sandbox-heartbeat", daemon=True,
    )
    heartbeat_thread.start()
    recovered = runtime.engine.recover_expired_leases()
    if recovered:
        _LOG.warning("Marked %d expired sandbox task lease(s) for reconciliation", recovered)

    _LOG.info("Sandbox worker started")
    while not stop_event.is_set():
        try:
            task = runtime.worker.run_once(lease_seconds=LEASE_SECONDS)
        except Exception:
            # Exception messages can contain task data. Keep operational logs
            # deliberately generic; durable task/audit records carry safe codes.
            _LOG.error("Sandbox task processing failed; inspect the durable task state")
            task = None
        if task is None:
            stop_event.wait(float(poll_interval))
        else:
            _LOG.info("Sandbox task reached state: %s", task.get("state", "unknown"))
    heartbeat_stop.set()
    heartbeat_thread.join(timeout=heartbeat_interval + 1.0)
    runtime.engine.stop_worker(worker_id)
    _LOG.info("Sandbox worker stopped after the current task boundary")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("YUKI_SANDBOX_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config()
    interval = _poll_interval(config)
    workspace_value = config.get("workspace")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise SystemExit("A configured workspace is required for the sandbox worker.")

    # The worker uses the same auth database as the web process, but is a
    # distinct OS process. It never binds a network listener.
    auth = BuiltInAuthService(str(Path(workspace_value).expanduser() / "auth.sqlite3"))
    # A process-unique lease owner prevents an expired old process from\n    # committing against a replacement process that reused the configured ID.\n    worker_config = dict(config)\n    base_worker_id = str(config.get("sandbox_execution_worker_id", "yuki-sandbox-worker"))\n    worker_config["sandbox_execution_worker_id"] = f"{base_worker_id[:220]}-{os.getpid()}-{secrets.token_hex(8)}"\n    runtime = build_sandbox_execution_runtime(worker_config, auth_service=auth)
    if runtime is None:
        raise SystemExit(
            "Sandbox execution is disabled. Set sandbox_execution_enabled: true "
            "and configure digest-pinned allowed images before starting this worker."
        )

    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_worker(runtime, stop_event=stop_event, poll_interval=interval)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
