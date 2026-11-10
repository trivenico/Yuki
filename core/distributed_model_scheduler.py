"""Deterministic resource-aware placement for independent model workloads.

This module creates a placement decision only. It never starts processes,
downloads model weights, dispatches arbitrary commands, or assumes devices
share memory. One workload is placed on one eligible paired device.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Any, Iterable


_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_MEMORY = 2**60


@dataclass(frozen=True)
class ModelRequirements:
    model_id: str
    memory_bytes: int
    gpu_memory_bytes: int = 0
    cpu_cores: int = 1
    require_gpu: bool = False
    cpu_millicores: int | None = None

    def __post_init__(self):
        if not isinstance(self.model_id, str) or not _MODEL_ID.fullmatch(self.model_id):
            raise ValueError("invalid_model_id")
        for name in ("memory_bytes", "gpu_memory_bytes"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= _MAX_MEMORY:
                raise ValueError("invalid_" + name)
        if type(self.cpu_cores) is not int or not 1 <= self.cpu_cores <= 4096:
            raise ValueError("invalid_cpu_cores")
        if self.cpu_millicores is not None and (
            type(self.cpu_millicores) is not int or not 1 <= self.cpu_millicores <= 4_096_000
        ):
            raise ValueError("invalid_cpu_millicores")
        if type(self.require_gpu) is not bool:
            raise ValueError("invalid_require_gpu")
        if self.require_gpu and self.gpu_memory_bytes == 0:
            raise ValueError("gpu_memory_required")


@dataclass(frozen=True)
class PlacementDecision:
    model_id: str
    device_id: str | None
    status: str
    reason: str
    eligible_device_ids: tuple[str, ...]
    rejected: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["eligible_device_ids"] = list(self.eligible_device_ids)
        result["rejected"] = list(self.rejected)
        return result


class DistributedModelScheduler:
    """Choose a single paired device using fresh registry state and explicit limits."""

    def __init__(self, *, max_devices: int = 100, allow_legacy_cpu_cores: bool = False):
        if type(max_devices) is not int or not 1 <= max_devices <= 1000:
            raise ValueError("max_devices must be between 1 and 1000")
        if type(allow_legacy_cpu_cores) is not bool:
            raise ValueError("invalid_legacy_cpu_policy")
        self.max_devices = max_devices
        self.allow_legacy_cpu_cores = allow_legacy_cpu_cores

    @staticmethod
    def _device(item: Any) -> tuple[str, dict[str, Any], str | None]:
        if not isinstance(item, dict):
            raise ValueError("invalid_device_record")
        device_id = item.get("device_id")
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise ValueError("invalid_device_id")
        if item.get("status") not in {"idle", "busy", "degraded"}:
            raise ValueError("invalid_device_status")
        if type(item.get("online")) is not bool:
            raise ValueError("invalid_device_online")
        capabilities = item.get("capabilities")
        if not isinstance(capabilities, dict):
            raise ValueError("invalid_device_capabilities")
        return device_id, capabilities, item.get("status")

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        if type(value) is int and 0 <= value <= _MAX_MEMORY:
            return value
        return None

    def plan(
        self,
        requirements: ModelRequirements,
        devices: Iterable[dict[str, Any]],
        *,
        allowed_device_ids: Iterable[str] | None = None,
    ) -> PlacementDecision:
        if not isinstance(requirements, ModelRequirements):
            raise TypeError("requirements must be ModelRequirements")
        if isinstance(devices, (str, bytes, dict)):
            raise TypeError("devices must be an iterable of device records")
        records = list(devices)
        if len(records) > self.max_devices:
            raise ValueError("device_limit_exceeded")
        allowlist = None
        if allowed_device_ids is not None:
            if isinstance(allowed_device_ids, (str, bytes)):
                raise TypeError("allowed_device_ids must be an iterable")
            allowlist = set()
            for value in allowed_device_ids:
                if not isinstance(value, str) or not _DEVICE_ID.fullmatch(value):
                    raise ValueError("invalid_allowed_device_id")
                allowlist.add(value)
        seen: set[str] = set()
        candidates: list[tuple[tuple[int, int, int, str], str]] = []
        rejected: list[dict[str, str]] = []
        for item in records:
            device_id, caps, status = self._device(item)
            if device_id in seen:
                raise ValueError("duplicate_device_id")
            seen.add(device_id)
            reason = None
            if allowlist is not None and device_id not in allowlist:
                reason = "not_in_allowlist"
            elif not item["online"]:
                reason = "offline"
            elif status != "idle":
                reason = "device_not_idle"
            required_millicores = (
                requirements.cpu_millicores
                if requirements.cpu_millicores is not None
                else requirements.cpu_cores * 1000
            )
            cpu = self._nonnegative_int(caps.get("cpu_millicores"))
            if cpu is None and self.allow_legacy_cpu_cores:
                legacy_cores = self._nonnegative_int(caps.get("cpu_cores"))
                cpu = legacy_cores * 1000 if legacy_cores is not None else None
            ram = self._nonnegative_int(caps.get("ram_available_bytes"))
            if reason is None and (cpu is None or cpu < required_millicores):
                reason = "insufficient_or_unknown_cpu"
            if reason is None and (ram is None or ram < requirements.memory_bytes):
                reason = "insufficient_or_unknown_ram"
            gpu_memory = 0
            gpus = caps.get("gpus", [])
            if not isinstance(gpus, list) or len(gpus) > 8:
                if reason is None:
                    reason = "invalid_gpu_inventory"
                gpus = []
            for gpu in gpus:
                if not isinstance(gpu, dict):
                    continue
                free = self._nonnegative_int(gpu.get("memory_available_bytes"))
                if free is not None:
                    gpu_memory = max(gpu_memory, free)
            if reason is None and requirements.require_gpu and not gpus:
                reason = "gpu_required"
            if reason is None and requirements.gpu_memory_bytes and gpu_memory < requirements.gpu_memory_bytes:
                reason = "insufficient_or_unknown_gpu_memory"
            if reason:
                rejected.append({"device_id": device_id, "reason": reason})
                continue
            # Prefer the smallest sufficient GPU/RAM headroom to avoid consuming
            # the largest node unnecessarily; stable ID is the final tie-breaker.
            ram_headroom = ram - requirements.memory_bytes
            gpu_headroom = gpu_memory - requirements.gpu_memory_bytes
            candidates.append(((ram_headroom, gpu_headroom, -cpu, device_id), device_id))
        candidates.sort(key=lambda pair: pair[0])
        eligible = tuple(device_id for _, device_id in candidates)
        if not eligible:
            return PlacementDecision(
                requirements.model_id, None, "unscheduled", "no_device_meets_requirements",
                (), tuple(sorted(rejected, key=lambda x: x["device_id"])),
            )
        return PlacementDecision(
            requirements.model_id, eligible[0], "planned",
            "best_fit_single_device; dispatch_not_performed",
            eligible, tuple(sorted(rejected, key=lambda x: x["device_id"])),
        )
