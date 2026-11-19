"""Hardware-independent compute contracts and bounded resource scheduling.

The CPU backend is the reference implementation. Memory capacity is an
operator-configured reservation budget, not a claim about free physical RAM.
Accelerator backends can implement ComputeBackend without changing callers.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
from typing import Callable, Iterator, TypeVar


T = TypeVar("T")


class ComputeFabricError(RuntimeError):
    """Base error for compute scheduling failures."""


class ComputeCapacityError(ComputeFabricError):
    """A request cannot be admitted within the configured resource budget."""


class ComputeBackendError(ComputeFabricError):
    """A backend contract or execution failed."""


@dataclass(frozen=True)
class ComputeCapabilities:
    backend: str
    device: str
    available: bool
    memory_capacity_bytes: int
    max_concurrency: int

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or not self.backend.strip():
            raise ValueError("backend must be non-empty text")
        if not isinstance(self.device, str) or self.device not in {"cpu", "cuda", "tpu"}:
            raise ValueError("device must be cpu, cuda, or tpu")
        if type(self.available) is not bool:
            raise TypeError("available must be a boolean")
        if type(self.memory_capacity_bytes) is not int or self.memory_capacity_bytes < 0:
            raise ValueError("memory_capacity_bytes must be a non-negative integer")
        if type(self.max_concurrency) is not int or self.max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")


@dataclass(frozen=True)
class ComputeRequest:
    """Caller-estimated resource requirements for one synchronous operation."""

    backend: str = "cpu"
    memory_bytes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or not self.backend.strip():
            raise ValueError("backend must be non-empty text")
        if type(self.memory_bytes) is not int or self.memory_bytes < 0:
            raise ValueError("memory_bytes must be a non-negative integer")


class ComputeBackend:
    """Backend contract; implementations must report truthful capabilities."""

    @property
    def capabilities(self) -> ComputeCapabilities:
        raise NotImplementedError

    def execute(self, operation: Callable[[], T]) -> T:
        raise NotImplementedError


class CPUBackend(ComputeBackend):
    """Synchronous CPU baseline with an explicit admission budget."""

    def __init__(
        self,
        *,
        memory_capacity_bytes: int = 512 * 1024 * 1024,
        max_concurrency: int = 1,
    ) -> None:
        self._capabilities = ComputeCapabilities(
            backend="cpu",
            device="cpu",
            available=True,
            memory_capacity_bytes=memory_capacity_bytes,
            max_concurrency=max_concurrency,
        )

    @property
    def capabilities(self) -> ComputeCapabilities:
        return self._capabilities

    def execute(self, operation: Callable[[], T]) -> T:
        if not callable(operation):
            raise TypeError("operation must be callable")
        return operation()


class ComputeScheduler:
    """Thread-safe admission controller with bounded memory and concurrency.

    Reservations are process-local. This scheduler does not detect hardware,
    allocate device memory, or coordinate multiple Yuki processes.
    """

    def __init__(self, backends: tuple[ComputeBackend, ...] = ()) -> None:
        self._lock = threading.Lock()
        self._backends: dict[str, ComputeBackend] = {}
        self._reserved_bytes: dict[str, int] = {}
        self._active: dict[str, int] = {}
        for backend in backends:
            self.register(backend)

    def register(self, backend: ComputeBackend) -> None:
        if not isinstance(backend, ComputeBackend):
            raise TypeError("backend must implement ComputeBackend")
        capabilities = backend.capabilities
        if not isinstance(capabilities, ComputeCapabilities):
            raise TypeError("backend capabilities must be ComputeCapabilities")
        name = capabilities.backend
        with self._lock:
            if name in self._backends:
                raise ValueError(f"backend already registered: {name}")
            self._backends[name] = backend
            self._reserved_bytes[name] = 0
            self._active[name] = 0

    def inventory(self) -> list[ComputeCapabilities]:
        with self._lock:
            return [
                backend.capabilities
                for _, backend in sorted(self._backends.items())
            ]

    @contextmanager
    def reserve(self, request: ComputeRequest) -> Iterator[ComputeBackend]:
        if not isinstance(request, ComputeRequest):
            raise TypeError("request must be a ComputeRequest")
        with self._lock:
            backend = self._backends.get(request.backend)
            if backend is None:
                raise LookupError(f"compute backend is not registered: {request.backend}")
            caps = backend.capabilities
            if not caps.available:
                raise ComputeBackendError(f"compute backend is unavailable: {request.backend}")
            current_bytes = self._reserved_bytes[request.backend]
            current_active = self._active[request.backend]
            if request.memory_bytes > caps.memory_capacity_bytes - current_bytes:
                raise ComputeCapacityError("compute memory reservation exceeds configured capacity")
            if current_active >= caps.max_concurrency:
                raise ComputeCapacityError("compute concurrency limit reached")
            self._reserved_bytes[request.backend] = current_bytes + request.memory_bytes
            self._active[request.backend] = current_active + 1

        try:
            yield backend
        finally:
            with self._lock:
                self._reserved_bytes[request.backend] -= request.memory_bytes
                self._active[request.backend] -= 1

    def execute(self, request: ComputeRequest, operation: Callable[[], T]) -> T:
        if not callable(operation):
            raise TypeError("operation must be callable")
        with self.reserve(request):
            backend = self._backends[request.backend]
            return backend.execute(operation)

    def stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                name: {
                    "reserved_memory_bytes": self._reserved_bytes[name],
                    "active_operations": self._active[name],
                }
                for name in sorted(self._backends)
            }
