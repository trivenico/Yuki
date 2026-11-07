"""Validated capability advertisements for paired Yuki compute workers.

This module validates metadata only. It does not authenticate a peer, open a
network listener, issue credentials, dispatch jobs, or grant permissions.
Transport authentication and pairing remain separate trust boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_GPU_VENDORS = {"nvidia", "amd", "apple", "intel", "other"}
_BACKENDS = {"ollama", "llama_cpp", "pytorch", "transformers", "onnx", "cpu"}
_MAX_BYTES = 2**60


class ComputeWorkerProtocolError(ValueError):
    """Invalid or unsafe compute-worker capability advertisement."""


def _positive_int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= _MAX_BYTES:
        raise ComputeWorkerProtocolError("invalid_" + field)
    return value


@dataclass(frozen=True)
class GPUCapability:
    vendor: str
    device_name: str
    memory_total_bytes: int
    memory_available_bytes: int
    compute_backend: str

    def __post_init__(self) -> None:
        if self.vendor not in _GPU_VENDORS:
            raise ComputeWorkerProtocolError("invalid_gpu_vendor")
        if not isinstance(self.device_name, str) or not self.device_name.strip() or len(self.device_name) > 160:
            raise ComputeWorkerProtocolError("invalid_gpu_device_name")
        _positive_int(self.memory_total_bytes, "gpu_memory_total")
        _positive_int(self.memory_available_bytes, "gpu_memory_available", allow_zero=True)
        if self.memory_available_bytes > self.memory_total_bytes:
            raise ComputeWorkerProtocolError("gpu_available_memory_exceeds_total")
        if self.compute_backend not in _BACKENDS:
            raise ComputeWorkerProtocolError("invalid_gpu_compute_backend")


@dataclass(frozen=True)
class ComputeWorkerCapabilities:
    worker_id: str
    protocol_version: int
    paired: bool
    enabled: bool
    cpu_cores: int
    ram_total_bytes: int
    ram_available_bytes: int
    storage_available_bytes: int
    backends: tuple[str, ...]
    gpus: tuple[GPUCapability, ...]
    last_seen_epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or not _ID.fullmatch(self.worker_id):
            raise ComputeWorkerProtocolError("invalid_worker_id")
        if type(self.protocol_version) is not int or self.protocol_version != 1:
            raise ComputeWorkerProtocolError("unsupported_protocol_version")
        if type(self.paired) is not bool or type(self.enabled) is not bool:
            raise ComputeWorkerProtocolError("invalid_worker_trust_state")
        _positive_int(self.cpu_cores, "cpu_cores")
        _positive_int(self.ram_total_bytes, "ram_total_bytes")
        _positive_int(self.ram_available_bytes, "ram_available_bytes", allow_zero=True)
        _positive_int(self.storage_available_bytes, "storage_available_bytes", allow_zero=True)
        if self.ram_available_bytes > self.ram_total_bytes:
            raise ComputeWorkerProtocolError("ram_available_exceeds_total")
        if not isinstance(self.backends, tuple) or not self.backends or len(self.backends) > len(_BACKENDS):
            raise ComputeWorkerProtocolError("invalid_worker_backends")
        if any(item not in _BACKENDS for item in self.backends) or len(set(self.backends)) != len(self.backends):
            raise ComputeWorkerProtocolError("invalid_worker_backends")
        if not isinstance(self.gpus, tuple) or len(self.gpus) > 8 or any(
            not isinstance(item, GPUCapability) for item in self.gpus
        ):
            raise ComputeWorkerProtocolError("invalid_worker_gpu_inventory")
        _positive_int(self.last_seen_epoch, "last_seen_epoch", allow_zero=True)

    def to_scheduler_record(self, *, now_epoch: int, max_age_seconds: int = 90) -> dict[str, Any]:
        """Return the existing scheduler's record shape, failing closed if stale/unpaired."""
        _positive_int(now_epoch, "now_epoch", allow_zero=True)
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
            raise ComputeWorkerProtocolError("invalid_max_age_seconds")
        age = now_epoch - self.last_seen_epoch
        online = age >= 0 and age <= max_age_seconds and self.paired and self.enabled
        return {
            "device_id": self.worker_id,
            "status": "idle" if online else "degraded",
            "online": online,
            "capabilities": {
                "cpu_cores": self.cpu_cores,
                "ram_available_bytes": self.ram_available_bytes,
                "storage_available_bytes": self.storage_available_bytes,
                "backends": list(self.backends),
                "gpus": [
                    {"vendor": gpu.vendor, "device_name": gpu.device_name,
                     "memory_total_bytes": gpu.memory_total_bytes,
                     "memory_available_bytes": gpu.memory_available_bytes,
                     "compute_backend": gpu.compute_backend}
                    for gpu in self.gpus
                ],
            },
        }
