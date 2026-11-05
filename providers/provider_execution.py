"""Cooperative provider execution deadlines and cancellation contracts.

A deadline can be enforced during a blocking provider call only when the provider
honors the optional execution_context. Legacy synchronous providers are checked
before and after the call; an overrun is reported as uncertain, not interrupted.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time


class ProviderExecutionError(RuntimeError):
    """Base class for provider execution control outcomes."""


class ProviderDeadlineExceeded(ProviderExecutionError):
    """Deadline elapsed; a side effect or inference may still have completed."""


class ProviderCancelled(ProviderExecutionError):
    """Caller requested cooperative cancellation."""


@dataclass(frozen=True)
class ProviderExecutionContext:
    deadline: float
    cancellation_event: threading.Event

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self) -> None:
        if self.cancellation_event.is_set():
            raise ProviderCancelled("provider execution was cancelled.")
        if self.remaining_seconds() <= 0:
            raise ProviderDeadlineExceeded(
                "provider execution deadline exceeded; outcome may be uncertain."
            )

    def wait_for_cancellation(self, timeout: float | None = None) -> bool:
        """Wait for cancellation, capped by the remaining execution deadline."""
        remaining = self.remaining_seconds()
        wait = remaining if timeout is None else min(remaining, max(0.0, timeout))
        return self.cancellation_event.wait(wait)


__all__ = [
    "ProviderCancelled", "ProviderDeadlineExceeded",
    "ProviderExecutionContext", "ProviderExecutionError",
]
