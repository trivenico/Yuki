"""Dedicated process entry point for the Yuki specialist worker service.

The web/API process never starts workers implicitly. Deployments provide a
trusted Python factory via YUKI_WORKER_SUPERVISOR_FACTORY=module:callable.
That callable must return a fully configured SpecialistWorkerSupervisor.
"""
from __future__ import annotations

import importlib
import os
import re
import signal
from typing import Callable

from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor

_FACTORY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


def load_supervisor_factory(spec: str) -> Callable[[], SpecialistWorkerSupervisor]:
    """Load an operator-configured factory; never evaluate a string as code."""
    if not isinstance(spec, str) or len(spec) > 240 or not _FACTORY.fullmatch(spec):
        raise ValueError("worker supervisor factory must use module:callable syntax.")
    module_name, attribute = spec.split(":", 1)
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise TypeError("configured worker supervisor factory is not callable.")
    return factory


def main() -> None:
    spec = os.environ.get("YUKI_WORKER_SUPERVISOR_FACTORY", "").strip()
    if not spec:
        raise SystemExit(
            "Set YUKI_WORKER_SUPERVISOR_FACTORY=trusted_module:create_supervisor."
        )
    mode = os.environ.get("YUKI_SANDBOX_EXECUTION_MODE", "direct").strip()
    if mode not in {"direct", "gate_first"}:
        raise SystemExit("YUKI_SANDBOX_EXECUTION_MODE must be direct or gate_first.")
    factory = load_supervisor_factory(spec)
    supervisor = factory()
    if not isinstance(supervisor, SpecialistWorkerSupervisor):
        raise TypeError("worker factory must return SpecialistWorkerSupervisor.")
    if mode == "gate_first":
        # Validate before worker registration or task claims; no direct fallback.
        from automation.gate_first_worker_deployment import validate_gate_first_supervisor
        supervisor = validate_gate_first_supervisor(supervisor)

    previous_handlers = {}

    def request_shutdown(_signum, _frame):
        supervisor.request_shutdown()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_shutdown)
        supervisor.run_until_shutdown()
    finally:
        # run_until_shutdown owns worker cleanup; close is idempotent and also
        # handles failures that occur before the supervisor enters its loop.
        try:
            supervisor.close()
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
