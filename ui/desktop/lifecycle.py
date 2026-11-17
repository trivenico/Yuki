"""Deterministic, retryable shutdown for native desktop resources."""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Iterable


class DesktopShutdownError(RuntimeError):
    """Raised after every owned resource has had a chance to close."""


class DesktopServiceLifecycle:
    """Close unique resources in reverse order; retain failed hooks for retry."""

    def __init__(self, resources: Iterable[Any]) -> None:
        self._resources = tuple(resources)
        self._closed_resources: set[int] = set()
        self._before_close_complete = False
        self._closed = False
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self, *, before_close: Callable[[], Any] | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            errors: list[Exception] = []
            if not self._before_close_complete:
                if before_close is None:
                    self._before_close_complete = True
                else:
                    try:
                        before_close()
                        self._before_close_complete = True
                    except Exception as exc:
                        errors.append(exc)

            seen: set[int] = set()
            all_resources_closed = True
            for resource in reversed(self._resources):
                if resource is None or id(resource) in seen:
                    continue
                seen.add(id(resource))
                if id(resource) in self._closed_resources:
                    continue
                close = getattr(resource, "close", None)
                if not callable(close):
                    self._closed_resources.add(id(resource))
                    continue
                try:
                    close()
                    self._closed_resources.add(id(resource))
                except Exception as exc:
                    all_resources_closed = False
                    errors.append(exc)

            expected_resources = {
                id(resource) for resource in self._resources if resource is not None
            }
            self._closed = (
                self._before_close_complete
                and all_resources_closed
                and expected_resources.issubset(self._closed_resources)
            )
            if errors:
                raise DesktopShutdownError(
                    f"{len(errors)} desktop shutdown hook(s) failed; "
                    "successful cleanup steps are retained and failed steps can be retried."
                ) from errors[0]
