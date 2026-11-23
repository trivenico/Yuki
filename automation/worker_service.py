"""Dedicated process entry point for the Yuki specialist worker service."""
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


def parse_execution_mode(value: str | None) -> str:
    """Parse the explicit sandbox mode; absence preserves legacy direct mode."""
    mode = "direct" if value is None else value.strip()
    if mode not in {"direct", "gate_first"}:
        raise ValueError("YUKI_SANDBOX_EXECUTION_MODE must be direct or gate_first.")
    return mode


def build_configured_supervisor(
    factory: Callable[[], SpecialistWorkerSupervisor], mode: str
) -> SpecialistWorkerSupervisor:
    """Construct and validate the supervisor before it can start workers."""
    if not callable(factory):
        raise TypeError("worker supervisor factory must be callable.")
    if mode not in {"direct", "gate_first"}:
        raise ValueError("unsupported worker sandbox execution mode.")
    supervisor = factory()
    if not isinstance(supervisor, SpecialistWorkerSupervisor):
        raise TypeError("worker factory must return SpecialistWorkerSupervisor.")
    if mode == "gate_first":
        from automation.gate_first_worker_deployment import validate_gate_first_supervisor
        supervisor = validate_gate_first_supervisor(supervisor)
    return supervisor


def main() -> None:
    spec = os.environ.get("YUKI_WORKER_SUPERVISOR_FACTORY", "").strip()
    if not spec:
        raise SystemExit("Set YUKI_WORKER_SUPERVISOR_FACTORY=trusted_module:create_supervisor.")
    try:
        mode = parse_execution_mode(os.environ.get("YUKI_SANDBOX_EXECUTION_MODE"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    factory = load_supervisor_factory(spec)
    supervisor = build_configured_supervisor(factory, mode)

    previous_handlers = {}

    def request_shutdown(_signum, _frame):
        supervisor.request_shutdown()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_shutdown)
        supervisor.run_until_shutdown()
    finally:
        try:
            supervisor.close()
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
