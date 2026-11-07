"""Conservative CPU-first execution profiles for memory-constrained devices.

This policy estimates safe local inference budgets. It does not probe GPUs,
download models, start runtimes, or guarantee that a particular model will fit.
"""
from __future__ import annotations

_GIB = 1024 ** 3
_MAX_MEMORY = 2 ** 60


class DeviceProfileError(ValueError):
    """Invalid device resource snapshot."""


def _memory(value, field, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or not 0 < value <= _MAX_MEMORY:
        raise DeviceProfileError("invalid_" + field)
    return value


def build_execution_profile(
    hardware_snapshot: dict,
    *,
    gpu_available_memory_bytes: int | None = None,
) -> dict:
    """Build an advisory profile from current host RAM and CPU inventory.

    For <=8 GiB devices, the model/runtime budget is capped at 35% of physical
    RAM and 50% of currently available RAM. This deliberately leaves headroom
    for the OS, desktop, Yuki orchestration and transient allocations.
    """
    if not isinstance(hardware_snapshot, dict):
        raise DeviceProfileError("invalid_hardware_snapshot")
    total = _memory(hardware_snapshot.get("total_memory_bytes"), "total_memory")
    available = _memory(hardware_snapshot.get("available_memory_bytes"), "available_memory")
    if available > total:
        raise DeviceProfileError("available_memory_exceeds_total")
    cpus = hardware_snapshot.get("logical_cpu_count")
    if type(cpus) is not int or not 1 <= cpus <= 4096:
        raise DeviceProfileError("invalid_logical_cpu_count")
    gpu = _memory(gpu_available_memory_bytes, "gpu_available_memory", optional=True)

    if total <= 8 * _GIB:
        tier, context, agents, thread_cap, total_ratio, available_ratio = (
            "compact_8gb", 2048, 1, 2, 0.35, 0.50
        )
    elif total <= 16 * _GIB:
        tier, context, agents, thread_cap, total_ratio, available_ratio = (
            "standard_16gb", 4096, 2, 4, 0.40, 0.55
        )
    else:
        tier, context, agents, thread_cap, total_ratio, available_ratio = (
            "expanded", 8192, 4, 8, 0.45, 0.60
        )

    budget = min(int(total * total_ratio), int(available * available_ratio))
    status = "ready_for_compact_cpu_inference" if budget >= 512 * 1024**2 else "insufficient_free_memory"
    return {
        "schema_version": 1,
        "profile": tier,
        "status": status,
        "execution_mode": "gpu_accelerated_available" if gpu is not None and gpu > 0 else "cpu_only",
        "cpu_inference_supported": True,
        "gpu_required": False,
        "logical_cpu_count": cpus,
        "recommended_cpu_threads": min(cpus, thread_cap),
        "total_memory_bytes": total,
        "available_memory_bytes": available,
        "model_runtime_memory_budget_bytes": budget,
        "recommended_context_tokens": context,
        "max_concurrent_agent_tasks": agents,
        "memory_policy": "conservative_dynamic_headroom",
        "gpu_available_memory_bytes": gpu,
        "model_fit_requires_runtime_measurement": True,
        "automatic_model_download": False,
        "automatic_model_activation": False,
        "limitations": [
            "This is a conservative planning profile, not a model compatibility guarantee.",
            "Actual fit depends on model architecture, quantization, context KV cache, runtime and OS pressure.",
            "CPU-only inference may be slower; use smaller quantized models and bounded context.",
            "Vision, speech and parallel agents may need to be disabled or queued on compact devices.",
        ],
    }
