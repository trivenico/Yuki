"""Deterministic shutdown for resources owned by the native desktop process."""
from __future__ import annotations

from threading import RLock
from typing import Any, Iterable


class DesktopShutdownError(RuntimeError):
    """Raised after every owned resource has had a chance to close."""


class DesktopServiceLifecycle:
    """Close unique service resources once, in reverse construction order."""

    def __init__(self, resources: Iterable[Any]) -> None:
        self._resources = tuple(resources)
        self._closed = False
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            seen: set[int] = set()
            errors: list[Exception] = []
            for resource in reversed(self._resources):
                if resource is None or id(resource) in seen:
                    continue
                seen.add(id(resource))
                close = getattr(resource, "close", None)
                if not callable(close):
                    continue
                try:
                    close()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise DesktopShutdownError(
                    f"{len(errors)} desktop resource(s) failed to close; "
                    "all remaining cleanup hooks were still attempted."
                ) from errors[0]
