"""Conservative model/device compatibility planning.

Depends on the hardware inventory contract introduced by PR #528.

This is a preflight estimator, not a runtime guarantee. It never loads a model,
downloads artifacts, changes devices, or selects a provider. Actual runtime
compatibility and live free memory must be independently verified.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from providers.hardware_discovery import HardwareInventory, ModelResourceEstimate


@dataclass(frozen=True)
class CompatibilityResult:
    status: Literal["compatible_gpu", "compatible_cpu", "compatible_hybrid", "incompatible", "insufficient_metadata"]
    recommended_execution: str | None
    estimated_memory_bytes: int | None
    usable_host_memory_bytes: int | None
    usable_gpu_memory_bytes: int | None
    required_disk_bytes: int
    available_disk_bytes: int | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    automatic_activation: bool = False


class ModelCompatibilityEngine:
    """Evaluate a model estimate against explicitly discovered device capacity."""

    def __init__(self, *, memory_reserve_ratio: float = 0.20, disk_reserve_ratio: float = 0.15):
        for name, value in (("memory_reserve_ratio", memory_reserve_ratio), ("disk_reserve_ratio", disk_reserve_ratio)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 0.8:
                raise ValueError(f"{name} must be between 0 and 0.8")
        self.memory_reserve_ratio = float(memory_reserve_ratio)
        self.disk_reserve_ratio = float(disk_reserve_ratio)

    def evaluate(
        self,
        inventory: HardwareInventory,
        estimate: ModelResourceEstimate,
        *,
        runtime_supported: bool | None,
        hybrid_supported: bool = False,
    ) -> CompatibilityResult:
        if not isinstance(inventory, HardwareInventory):
            raise TypeError("inventory must be a HardwareInventory")
        if not isinstance(estimate, ModelResourceEstimate):
            raise TypeError("estimate must be a ModelResourceEstimate")
        if runtime_supported not in (True, False, None):
            raise TypeError("runtime_supported must be True, False, or None")
        if type(hybrid_supported) is not bool:
            raise TypeError("hybrid_supported must be a boolean")

        host_total = inventory.physical_memory_bytes
        host_usable = int(host_total * (1 - self.memory_reserve_ratio)) if host_total else None
        gpu_capacities = [
            gpu.memory_available_bytes if gpu.memory_available_bytes is not None else gpu.memory_total_bytes
            for gpu in inventory.gpus
        ]
        # Do not sum multiple GPUs: peer access and tensor parallelism are
        # runtime/model dependent and cannot be inferred from inventory alone.
        gpu_usable = None
        known_gpu = [value for value in gpu_capacities if isinstance(value, int) and value > 0]
        if known_gpu:
            gpu_usable = int(max(known_gpu) * (1 - self.memory_reserve_ratio))

        disk_available = inventory.storage_free_bytes
        disk_required = estimate.disk_bytes_estimate
        memory_required = estimate.total_memory_bytes_estimate
        reasons: list[str] = []
        warnings: list[str] = []

        if memory_required is None or host_usable is None:
            reasons.append("insufficient_memory_metadata")
            warnings.append("A complete memory estimate and detected host RAM are required for a capacity decision.")
            return CompatibilityResult(
                "insufficient_metadata", None, memory_required, host_usable,
                gpu_usable, disk_required, disk_available, tuple(reasons),
                tuple(warnings),
            )

        if runtime_supported is False:
            reasons.append("runtime_model_incompatibility")
            return CompatibilityResult(
                "incompatible", None, memory_required, host_usable,
                gpu_usable, disk_required, disk_available, tuple(reasons),
                ("The selected runtime explicitly reports this model as unsupported.",),
            )
        if runtime_supported is None:
            warnings.append("Runtime/model compatibility has not been verified.")
        if disk_available is None:
            warnings.append("Free storage was not measured; disk capacity cannot be confirmed.")
        elif disk_required > int(disk_available * (1 - self.disk_reserve_ratio)):
            reasons.append("insufficient_disk_capacity")
            return CompatibilityResult(
                "incompatible", None, memory_required, host_usable,
                gpu_usable, disk_required, disk_available, tuple(reasons),
                tuple(warnings),
            )

        # A full-device fit is preferred. Hybrid is considered only when the
        # caller has verified that the chosen runtime supports CPU/GPU offload.
        if gpu_usable is not None and gpu_usable >= memory_required:
            status, execution = "compatible_gpu", "gpu"
        elif host_usable >= memory_required:
            status, execution = "compatible_cpu", "cpu"
        elif hybrid_supported and gpu_usable is not None and host_usable + gpu_usable >= memory_required:
            status, execution = "compatible_hybrid", "hybrid_cpu_gpu"
            warnings.append("Hybrid fit uses aggregate capacity only; actual layer placement, KV-cache placement and runtime workspace must be benchmarked.")
        else:
            reasons.append("insufficient_memory_capacity")
            if gpu_usable is None:
                warnings.append("No usable GPU memory was detected; GPU compatibility is unknown.")
            if not hybrid_supported:
                warnings.append("Hybrid CPU/GPU execution was not enabled or verified.")
            return CompatibilityResult(
                "incompatible", None, memory_required, host_usable,
                gpu_usable, disk_required, disk_available, tuple(reasons),
                tuple(warnings),
            )

        if runtime_supported is None:
            warnings.append("Capacity appears sufficient, but runtime support must be verified before activation.")
        warnings.append("Capacity is an estimate; run a bounded benchmark before approving activation.")
        return CompatibilityResult(
            status, execution, memory_required, host_usable, gpu_usable,
            disk_required, disk_available, tuple(reasons), tuple(warnings),
        )


def compatibility_to_dict(result: CompatibilityResult) -> dict:
    """Return a JSON-serializable preflight report."""
    return asdict(result)
