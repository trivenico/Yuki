"""Run one deployment-owned PostgreSQL worker runtime in a supervised process.

The bootstrap module is deployment code and must return an already composed
ProductionWorkerRuntime. This entrypoint never creates credentials, runs
migrations, or starts worker threads.
"""
from __future__ import annotations

import importlib
import os
import signal
import threading
from collections.abc import Callable
from typing import Any

from automation.production_worker_runtime import ProductionWorkerRuntime
from core.production_security_runtime import ProductionSecurityRuntime


def _load_factory(spec: str) -> Callable[[], Any]:
    if not isinstance(spec, str) or ":" not in spec:
        raise RuntimeError("worker_bootstrap_must_be_module_colon_callable")
    module_name, attribute = spec.split(":", 1)
    if not module_name or not attribute or "." in attribute:
        raise RuntimeError("worker_bootstrap_spec_invalid")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise RuntimeError("worker_bootstrap_factory_not_callable")
    return factory


def load_production_runtime(factory: Callable[[], Any]) -> ProductionWorkerRuntime:
    if not callable(factory):
        raise TypeError("worker_bootstrap_factory_required")
    runtime = factory()
    if not isinstance(runtime, ProductionWorkerRuntime):
        raise TypeError("bootstrap_must_return_production_worker_runtime")
    if not isinstance(runtime.security_runtime, ProductionSecurityRuntime):
        raise TypeError("worker_runtime_must_own_deployment_security_runtime")
    try:
        runtime.verify_readiness()
    except Exception:
        # The bootstrap has transferred ownership by returning the composed runtime.
        # Do not leak its pools if the first readiness probe fails.
        try:
            runtime.close()
        except Exception:
            pass
        close_security = getattr(runtime.security_runtime, "close", None)
        if callable(close_security):
            try:
                close_security()
            except Exception:
                pass
        raise
    return runtime


def run_worker_service(
    runtime: ProductionWorkerRuntime,
    stop_event: threading.Event,
    *,
    cycle_interval_seconds: float = 2.0,
) -> int:
    """Run finite cycles until stopped; always close worker pools before security."""
    if not isinstance(runtime, ProductionWorkerRuntime):
        raise TypeError("production_worker_runtime_required")
    if not isinstance(stop_event, threading.Event):
        raise TypeError("threading_stop_event_required")
    if isinstance(cycle_interval_seconds, bool) or not isinstance(cycle_interval_seconds, (int, float)) or not 0.1 <= cycle_interval_seconds <= 300:
        raise ValueError("worker_cycle_interval_out_of_range")
    try:
        runtime.verify_readiness()
        while not stop_event.is_set():
            runtime.run_cycle()
            stop_event.wait(cycle_interval_seconds)
        return 0
    finally:
        errors = []
        try:
            runtime.close()
        except Exception as exc:
            errors.append(exc)
        try:
            runtime.security_runtime.close()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError("worker_service_shutdown_incomplete") from errors[0]


def main() -> int:
    if os.environ.get("YUKI_ENABLE_DISTRIBUTED_WORKERS", "false").strip().lower() != "true":
        raise SystemExit("distributed_workers_disabled_set_YUKI_ENABLE_DISTRIBUTED_WORKERS=true")
    factory_spec = os.environ.get("YUKI_WORKER_BOOTSTRAP", "").strip()
    if not factory_spec:
        raise SystemExit("YUKI_WORKER_BOOTSTRAP_module_colon_callable_required")
    interval_raw = os.environ.get("YUKI_WORKER_CYCLE_INTERVAL_SECONDS", "2")
    try:
        interval = float(interval_raw)
    except ValueError as exc:
        raise SystemExit("YUKI_WORKER_CYCLE_INTERVAL_SECONDS_must_be_numeric") from exc
    if not 0.1 <= interval <= 300:
        raise SystemExit("YUKI_WORKER_CYCLE_INTERVAL_SECONDS_out_of_range")

    runtime = load_production_runtime(_load_factory(factory_spec))
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        return run_worker_service(runtime, stop_event, cycle_interval_seconds=interval)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
