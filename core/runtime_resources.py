"""Explicit ownership and reverse-order shutdown for host-composed resources."""
from __future__ import annotations

from threading import RLock
from typing import Any, Iterable


class RuntimeResourceGroup:
    """Own resources transferred by a host and close them once in reverse order.

    Resources without a callable close method are valid: many Yuki services
    open SQLite connections per operation and therefore have no persistent
    handle to release. Ownership is still recorded so future closeable clients
    are included in the same lifecycle contract.
    """

    def __init__(self, resources: Iterable[Any] = ()):
        self._resources: list[Any] = []
        self._identities: set[int] = set()
        self._closed = False
        self._lock = RLock()
        for resource in resources:
            self.add(resource)

    def add(self, resource: Any) -> Any:
        if resource is None:
            raise TypeError("runtime resource cannot be None")
        with self._lock:
            if self._closed:
                raise RuntimeError("runtime resource group is already closed")
            identity = id(resource)
            if identity not in self._identities:
                self._identities.add(identity)
                self._resources.append(resource)
        return resource

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            resources = tuple(reversed(self._resources))
            self._resources.clear()
            self._identities.clear()

        first_error: BaseException | None = None
        for resource in resources:
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except BaseException as exc:
                # Process-control exceptions must propagate, but only after every
                # owned resource has had its reverse-order cleanup attempted.
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            if not isinstance(first_error, Exception):
                raise first_error
            raise RuntimeError("one or more owned runtime resources failed to close") from first_error
