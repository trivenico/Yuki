"""Hardware-independent compute contracts and bounded resource scheduling.

The CPU backend is the reference implementation. Memory capacity is an
operator-configured reservation budget, not a claim about free physical RAM.
Accelerator backends can implement ComputeBackend without changing callers.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib
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


class CUDABackend(ComputeBackend):
    """Optional NVIDIA CUDA backend using an installed PyTorch runtime.

    The reported capacity is the free VRAM snapshot taken at construction;
    deployments should recreate/refresh the backend after device topology or
    external GPU workloads change. Reservations remain process-local.
    """

    def __init__(
        self, *, device_index: int = 0, max_concurrency: int = 1,
        torch_module=None,
    ) -> None:
        if type(device_index) is not int or device_index < 0:
            raise ValueError("device_index must be a non-negative integer")
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        self.device_index = device_index
        self._torch = torch_module
        if self._torch is None:
            try:
                self._torch = importlib.import_module("torch")
            except ImportError:
                self._torch = None
        available = False
        capacity = 0
        device_name = f"cuda:{device_index}"
        if self._torch is not None:
            try:
                cuda = self._torch.cuda
                available = bool(cuda.is_available() and device_index < cuda.device_count())
                if available:
                    device_name = str(cuda.get_device_name(device_index))
                    free_bytes, _total_bytes = cuda.mem_get_info(device_index)
                    capacity = max(0, int(free_bytes))
            except Exception:
                # Discovery is diagnostic; a broken driver/runtime is unavailable.
                available = False
                capacity = 0
        self._capabilities = ComputeCapabilities(
            backend="cuda", device="cuda", available=available,
            memory_capacity_bytes=capacity, max_concurrency=max_concurrency,
        )
        self.device_name = device_name

    @property
    def capabilities(self) -> ComputeCapabilities:
        return self._capabilities

    def execute(self, operation: Callable[[], T]) -> T:
        if not callable(operation):
            raise TypeError("operation must be callable")
        if not self._capabilities.available or self._torch is None:
            raise ComputeBackendError("NVIDIA CUDA runtime is unavailable")
        try:
            with self._torch.cuda.device(self.device_index):
                return operation()
        except Exception as exc:
            raise ComputeBackendError("CUDA operation failed") from exc


class GoogleTPUBackend(ComputeBackend):
    """Optional Google TPU backend using JAX device placement.

    JAX does not expose a portable, reliable free-HBM query across TPU
    generations. Capacity is therefore an explicit operator-supplied budget,
    never a fabricated hardware measurement.
    """

    def __init__(
        self, *, memory_capacity_bytes: int, max_concurrency: int = 1,
        device_index: int = 0, jax_module=None,
    ) -> None:
        if type(memory_capacity_bytes) is not int or memory_capacity_bytes < 0:
            raise ValueError("memory_capacity_bytes must be a non-negative integer")
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        if type(device_index) is not int or device_index < 0:
            raise ValueError("device_index must be a non-negative integer")
        self.device_index = device_index
        self._jax = jax_module
        if self._jax is None:
            try:
                self._jax = importlib.import_module("jax")
            except ImportError:
                self._jax = None
        self._device = None
        available = False
        if self._jax is not None:
            try:
                devices = self._jax.devices("tpu")
                # JAX platform spelling is "tpu"; tolerate runtime normalization.
                if not devices:
                    devices = [device for device in self._jax.devices()
                               if str(getattr(device, "platform", "")).lower() == "tpu"]
                if device_index < len(devices):
                    self._device = devices[device_index]
                    available = True
            except Exception:
                available = False
        self._capabilities = ComputeCapabilities(
            backend="tpu", device="tpu", available=available,
            memory_capacity_bytes=memory_capacity_bytes if available else 0,
            max_concurrency=max_concurrency,
        )

    @property
    def capabilities(self) -> ComputeCapabilities:
        return self._capabilities

    def execute(self, operation: Callable[[], T]) -> T:
        if not callable(operation):
            raise TypeError("operation must be callable")
        if not self._capabilities.available or self._jax is None or self._device is None:
            raise ComputeBackendError("Google TPU runtime is unavailable")
        try:
            with self._jax.default_device(self._device):
                return operation()
        except Exception as exc:
            raise ComputeBackendError("TPU operation failed") from exc


class ModelResidencyLease:
    """Long-lived memory admission held until model unload is confirmed.

    A failed/uncertain unload poisons the lease. Only explicit scheduler
    reconciliation may release a poisoned lease after the runtime is known
    to have relinquished the model allocation.
    """

    def __init__(self, scheduler, backend: str, model_id: str, memory_bytes: int):
        self._scheduler = scheduler
        self._backend = backend
        self._model_id = model_id
        self._memory_bytes = memory_bytes
        self._state = "active"

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def memory_bytes(self) -> int:
        return self._memory_bytes

    @property
    def state(self) -> str:
        with self._scheduler._lock:
            return self._state

    def release(self) -> None:
        """Release after successful unload; repeated release is harmless."""
        with self._scheduler._lock:
            if self._state == "released":
                return
            if self._state == "poisoned":
                raise ComputeBackendError(
                    "poisoned model residency requires explicit reconciliation"
                )
            self._scheduler._resident_bytes[self.backend] -= self.memory_bytes
            self._scheduler._resident_models[self.backend] -= 1
            self._state = "released"

    def mark_unload_failed(self) -> None:
        """Retain admission when unload may have left device memory resident."""
        with self._scheduler._lock:
            if self._state == "released":
                raise ComputeBackendError("cannot poison a released residency lease")
            self._state = "poisoned"



class ModelSessionError(ComputeFabricError):
    """A model session is not in a state that permits the requested operation."""


class ModelSession:
    """Opaque manager-owned model handle with serialized lifecycle operations."""

    def __init__(self, manager, model_id: str, request: ComputeRequest,
                 backend: ComputeBackend, lease: ModelResidencyLease, handle):
        self._manager = manager
        self.model_id = model_id
        self.request = request
        self.backend = backend
        self._lease = lease
        self._handle = handle
        self._state = "ready"
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state


class ModelLifecycleManager:
    """Owns admission, load, inference, and unload around a runtime adapter.

    The adapter is injected as callbacks so this layer remains independent of
    PyTorch, JAX, and provider implementations. Calls are serialized per model.
    """

    def __init__(self, scheduler: ComputeScheduler):
        if not isinstance(scheduler, ComputeScheduler):
            raise TypeError("scheduler must be a ComputeScheduler")
        self._scheduler = scheduler

    def load(self, request: ComputeRequest, *, model_id: str,
             loader: Callable[[ComputeBackend], T],
             cleanup_failed_load: Callable[[ComputeBackend, str], None] | None = None
             ) -> ModelSession:
        if not callable(loader):
            raise TypeError("loader must be callable")
        if cleanup_failed_load is not None and not callable(cleanup_failed_load):
            raise TypeError("cleanup_failed_load must be callable")
        lease = self._scheduler.reserve_model(request, model_id=model_id)
        try:
            backend = self._scheduler.backend_for(request.backend)
        except BaseException:
            # Backend availability may change after admission. No runtime load
            # has started, so release the reservation rather than poisoning it.
            lease.release()
            raise
        try:
            handle = backend.execute(lambda: loader(backend))
        except BaseException as load_error:
            if cleanup_failed_load is None:
                lease.mark_unload_failed()
            else:
                try:
                    backend.execute(lambda: cleanup_failed_load(backend, model_id))
                except BaseException as cleanup_error:
                    lease.mark_unload_failed()
                    raise load_error from cleanup_error
                else:
                    lease.release()
            raise
        return ModelSession(self, model_id, request, backend, lease, handle)

    def infer(self, session: ModelSession, request: ComputeRequest,
              operation: Callable[[object], T]) -> T:
        if not isinstance(session, ModelSession) or session._manager is not self:
            raise TypeError("session must belong to this manager")
        if not isinstance(request, ComputeRequest):
            raise TypeError("request must be a ComputeRequest")
        if request.backend != session.request.backend:
            raise ModelSessionError("inference backend must match model residency backend")
        if not callable(operation):
            raise TypeError("operation must be callable")
        with session._lock:
            if session._state != "ready":
                raise ModelSessionError(f"model session is not ready: {session._state}")
            with self._scheduler.reserve(request) as backend:
                return backend.execute(lambda: operation(session._handle))

    def unload(self, session: ModelSession,
               unloader: Callable[[ComputeBackend, object], None]) -> None:
        if not isinstance(session, ModelSession) or session._manager is not self:
            raise TypeError("session must belong to this manager")
        if not callable(unloader):
            raise TypeError("unloader must be callable")
        with session._lock:
            if session._state == "unloaded":
                return
            if session._state == "unhealthy":
                raise ModelSessionError("unhealthy model session requires reconciliation")
            if session._state != "ready":
                raise ModelSessionError(f"model session cannot unload from {session._state}")
            session._state = "unloading"
            try:
                session.backend.execute(
                    lambda: unloader(session.backend, session._handle)
                )
            except BaseException:
                session._state = "unhealthy"
                session._lease.mark_unload_failed()
                raise
            session._handle = None
            session._lease.release()
            session._state = "unloaded"

    def reconcile_unloaded(self, session: ModelSession) -> None:
        """Release poisoned admission only after caller confirms runtime cleanup."""
        if not isinstance(session, ModelSession) or session._manager is not self:
            raise TypeError("session must belong to this manager")
        with session._lock:
            if session._state == "unloaded":
                return
            if session._state != "unhealthy":
                raise ModelSessionError("only unhealthy sessions can be reconciled")
            self._scheduler.confirm_model_unloaded(session._lease)
            session._handle = None
            session._state = "unloaded"


class ComputeScheduler:
    """Thread-safe admission controller with bounded memory and concurrency.

    Reservations are process-local. This scheduler does not detect hardware,
    allocate device memory, or coordinate multiple Yuki processes.
    """

    def __init__(self, backends: tuple[ComputeBackend, ...] = ()) -> None:
        self._lock = threading.Lock()
        self._backends: dict[str, ComputeBackend] = {}
        self._reserved_bytes: dict[str, int] = {}
        self._resident_bytes: dict[str, int] = {}
        self._resident_models: dict[str, int] = {}
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
            self._resident_bytes[name] = 0
            self._resident_models[name] = 0
            self._active[name] = 0

    def backend_for(self, backend_name: str) -> ComputeBackend:
        """Return a registered backend for controlled lifecycle orchestration."""
        with self._lock:
            backend = self._backends.get(backend_name)
            if backend is None:
                raise LookupError(f"compute backend is not registered: {backend_name}")
            if not backend.capabilities.available:
                raise ComputeBackendError(f"compute backend is unavailable: {backend_name}")
            return backend

    def inventory(self) -> list[ComputeCapabilities]:
        with self._lock:
            return [
                backend.capabilities
                for _, backend in sorted(self._backends.items())
            ]

    def reserve_model(self, request: ComputeRequest, *, model_id: str) -> ModelResidencyLease:
        """Admit persistent model memory before loading weights.

        This lease does not consume an operation-concurrency slot. Each
        inference must still use reserve() for transient memory/concurrency.
        """
        if not isinstance(request, ComputeRequest):
            raise TypeError("request must be a ComputeRequest")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 256:
            raise ValueError("model_id must be non-empty text of at most 256 characters")
        with self._lock:
            backend = self._backends.get(request.backend)
            if backend is None:
                raise LookupError(f"compute backend is not registered: {request.backend}")
            caps = backend.capabilities
            if not caps.available:
                raise ComputeBackendError(f"compute backend is unavailable: {request.backend}")
            committed = self._resident_bytes[request.backend] + self._reserved_bytes[request.backend]
            if request.memory_bytes > caps.memory_capacity_bytes - committed:
                raise ComputeCapacityError("model residency exceeds configured capacity")
            self._resident_bytes[request.backend] += request.memory_bytes
            self._resident_models[request.backend] += 1
            return ModelResidencyLease(self, request.backend, model_id, request.memory_bytes)

    def confirm_model_unloaded(self, lease: ModelResidencyLease) -> None:
        """Operator/runtime reconciliation after a poisoned lease's allocation is gone."""
        if not isinstance(lease, ModelResidencyLease) or lease._scheduler is not self:
            raise TypeError("lease must belong to this scheduler")
        with self._lock:
            if lease._state == "released":
                return
            if lease._state != "poisoned":
                raise ComputeBackendError("only poisoned leases require reconciliation")
            self._resident_bytes[lease.backend] -= lease.memory_bytes
            self._resident_models[lease.backend] -= 1
            lease._state = "released"

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
            committed_bytes = self._resident_bytes[request.backend] + current_bytes
            if request.memory_bytes > caps.memory_capacity_bytes - committed_bytes:
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
                    "reserved_memory_bytes": self._reserved_bytes[name] + self._resident_bytes[name],
                    "resident_memory_bytes": self._resident_bytes[name],
                    "transient_memory_bytes": self._reserved_bytes[name],
                    "resident_models": self._resident_models[name],
                    "active_operations": self._active[name],
                }
                for name in sorted(self._backends)
            }
