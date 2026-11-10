"""Explicit lifecycle supervisor for an application-managed inference worker.

No background thread, process, timer or listener is started by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from typing import Callable


@dataclass(frozen=True)
class InferenceSupervisorStats:
    claimed: int
    succeeded: int
    failed: int
    idle_polls: int
    transient_errors: int
    stopped_reason: str


class InferenceWorkerSupervisor:
    def __init__(
        self,
        worker,
        *,
        idle_interval: float = 1.0,
        error_backoff_initial: float = 1.0,
        error_backoff_max: float = 30.0,
        max_consecutive_errors: int = 8,
        stop_event=None,
        wait: Callable[[float], object] | None = None,
        logger=None,
    ):
        if not callable(getattr(worker, "run_once", None)):
            raise TypeError("worker_must_provide_run_once")
        for name, value, low, high in (
            ("idle_interval", idle_interval, 0.05, 60.0),
            ("error_backoff_initial", error_backoff_initial, 0.05, 60.0),
            ("error_backoff_max", error_backoff_max, 0.05, 300.0),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError("invalid_" + name)
        if error_backoff_max < error_backoff_initial:
            raise ValueError("error_backoff_max_must_be_at_least_initial")
        if type(max_consecutive_errors) is not int or not 1 <= max_consecutive_errors <= 100:
            raise ValueError("invalid_max_consecutive_errors")
        self.worker = worker
        self.idle_interval = float(idle_interval)
        self.error_backoff_initial = float(error_backoff_initial)
        self.error_backoff_max = float(error_backoff_max)
        self.max_consecutive_errors = max_consecutive_errors
        self.stop_event = stop_event or threading.Event()
        if not callable(getattr(self.stop_event, "is_set", None)):
            raise TypeError("stop_event_must_provide_is_set")
        self._wait = wait or self.stop_event.wait
        if not callable(self._wait):
            raise TypeError("wait_must_be_callable")
        self.logger = logger or logging.getLogger(__name__)

    def run_forever(self) -> InferenceSupervisorStats:
        claimed = succeeded = failed = idle = errors = consecutive = 0
        reason = "stop_requested"
        while not self.stop_event.is_set():
            try:
                result = self.worker.run_once()
                consecutive = 0
                if not getattr(result, "claimed", False):
                    idle += 1
                    self._wait(self.idle_interval)
                    continue
                claimed += 1
                if getattr(result, "status", None) == "succeeded":
                    succeeded += 1
                else:
                    failed += 1
            except PermissionError:
                reason = "device_authentication_failed"
                self.logger.warning("Inference worker stopped: device authentication failed")
                break
            except Exception:
                errors += 1
                consecutive += 1
                self.logger.exception("Inference worker encountered a transient error")
                if consecutive >= self.max_consecutive_errors:
                    reason = "consecutive_error_limit"
                    break
                delay = min(
                    self.error_backoff_max,
                    self.error_backoff_initial * (2 ** (consecutive - 1)),
                )
                self._wait(delay)
        return InferenceSupervisorStats(claimed, succeeded, failed, idle, errors, reason)
