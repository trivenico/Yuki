"""Conservative host/GPU memory budgeting for local model placement.

The planner is advisory only. It never downloads, loads, activates, or dispatches
models. GPU memory must come from a trusted runtime/driver probe supplied by the
caller; absent telemetry is treated as unknown, never as unlimited capacity.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from providers.ollama_model_inventory import InstalledModel, estimate_requirements

_GIB = 1024 ** 3
_MAX_MEMORY = 2 ** 60


def _parse_meminfo(text: str) -> tuple[int, int]:
    """Parse Linux MemTotal/MemAvailable values (kB) without trusting extras."""
    if not isinstance(text, str) or len(text) > 100_000:
        raise ValueError("invalid_meminfo")
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in {"MemTotal:", "MemAvailable:"} and parts[2] == "kB":
            try:
                value = int(parts[1])
            except ValueError:
                continue
            if value > 0:
                values[parts[0]] = value * 1024
    if "MemTotal:" not in values or "MemAvailable:" not in values:
        raise ValueError("host_memory_telemetry_unavailable")
    return values["MemTotal:"], values["MemAvailable:"]


def detect_host_memory() -> dict:
    """Return conservative host memory telemetry using OS-provided counters.

    Linux cgroup v2 limits are applied to the physical MemAvailable value.
    On other POSIX platforms, available pages are used as a conservative lower
    bound. No subprocesses, network requests, or optional packages are needed.
    """
    if os.name == "posix" and Path("/proc/meminfo").is_file():
        total, available = _parse_meminfo(Path("/proc/meminfo").read_text(encoding="ascii"))
        source = "linux_proc_meminfo"
        try:
            limit_text = Path("/sys/fs/cgroup/memory.max").read_text(encoding="ascii").strip()
            current_text = Path("/sys/fs/cgroup/memory.current").read_text(encoding="ascii").strip()
            if limit_text != "max":
                limit, current = int(limit_text), int(current_text)
                if 0 < limit <= _MAX_MEMORY and 0 <= current <= _MAX_MEMORY:
                    total = min(total, limit)
                    available = min(available, max(0, limit - current))
                    source = "linux_proc_meminfo_cgroup_v2"
        except (OSError, ValueError):
            pass
        return {"total_memory_bytes": total, "available_memory_bytes": min(total, available), "source": source}

    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        if page_size > 0 and total_pages > 0 and 0 <= available_pages <= total_pages:
            return {
                "total_memory_bytes": page_size * total_pages,
                "available_memory_bytes": page_size * available_pages,
                "source": "os_sysconf_conservative_available_pages",
            }
    except (AttributeError, OSError, ValueError):
        pass
    raise RuntimeError("host_memory_telemetry_unavailable")


def _memory_bytes(value: int, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= _MAX_MEMORY:
        raise ValueError(f"invalid_{field}")
    return value


@dataclass(frozen=True)
class GPUResource:
    """One GPU's currently free memory, as reported by a trusted local probe."""
    name: str
    free_memory_bytes: int
    vendor: str = "unknown"

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 160:
            raise ValueError("invalid_gpu_name")
        _memory_bytes(self.free_memory_bytes, "gpu_free_memory", allow_zero=True)
        if self.vendor not in {"nvidia", "amd", "intel", "apple", "unknown"}:
            raise ValueError("invalid_gpu_vendor")


@dataclass(frozen=True)
class HardwareSnapshot:
    """Point-in-time memory telemetry; unknown values must remain None."""
    total_memory_bytes: int
    available_memory_bytes: int
    gpus: tuple[GPUResource, ...] = ()

    def __post_init__(self):
        _memory_bytes(self.total_memory_bytes, "total_memory")
        _memory_bytes(self.available_memory_bytes, "available_memory", allow_zero=True)
        if self.available_memory_bytes > self.total_memory_bytes:
            raise ValueError("available_memory_exceeds_total")
        if not isinstance(self.gpus, tuple) or len(self.gpus) > 16:
            raise ValueError("invalid_gpu_inventory")
        if any(not isinstance(gpu, GPUResource) for gpu in self.gpus):
            raise ValueError("invalid_gpu_inventory")


@dataclass(frozen=True)
class ResourcePolicy:
    """Operator-controlled reserves; defaults protect the running OS."""
    host_reserve_bytes: int = 2 * _GIB
    gpu_reserve_bytes: int = 512 * 1024 ** 2
    runtime_overhead_ratio: float = 0.25
    safety_margin_ratio: float = 0.15

    def __post_init__(self):
        _memory_bytes(self.host_reserve_bytes, "host_reserve", allow_zero=True)
        _memory_bytes(self.gpu_reserve_bytes, "gpu_reserve", allow_zero=True)
        for field, value in (
            ("runtime_overhead_ratio", self.runtime_overhead_ratio),
            ("safety_margin_ratio", self.safety_margin_ratio),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 2:
                raise ValueError(f"invalid_{field}")


def plan_model_placement(
    model: InstalledModel,
    hardware: HardwareSnapshot,
    *,
    policy: ResourcePolicy = ResourcePolicy(),
    gpu_offload_ratio: float | None = None,
) -> dict:
    """Return feasible CPU/GPU/hybrid choices without activating anything.

    Model memory is estimated from the installed artifact size. This is a
    deliberately conservative heuristic, not a measurement of runtime KV cache,
    context length, allocator fragmentation, or backend-specific overhead.
    """
    if not isinstance(model, InstalledModel) or not isinstance(hardware, HardwareSnapshot):
        raise TypeError("model_and_hardware_must_be_typed")
    if not isinstance(policy, ResourcePolicy):
        raise TypeError("policy_must_be_ResourcePolicy")
    if gpu_offload_ratio is not None and (
        isinstance(gpu_offload_ratio, bool)
        or not isinstance(gpu_offload_ratio, (int, float))
        or not 0 < gpu_offload_ratio <= 1
    ):
        raise ValueError("invalid_gpu_offload_ratio")

    base = estimate_requirements(
        model, runtime_overhead_ratio=policy.runtime_overhead_ratio,
        gpu_offload_ratio=0,
    )["estimated_host_memory_bytes"]
    # Include an additional margin after runtime overhead.
    total_estimate = int(base * (1 + policy.safety_margin_ratio))
    host_budget = max(0, hardware.available_memory_bytes - policy.host_reserve_bytes)
    cpu_feasible = total_estimate <= host_budget

    choices = []
    if cpu_feasible:
        choices.append({
            "placement": "cpu",
            "estimated_host_memory_bytes": total_estimate,
            "estimated_gpu_memory_bytes": 0,
            "feasible": True,
            "reason": "host_memory_budget_satisfied",
        })

    if gpu_offload_ratio is not None:
        for gpu in hardware.gpus:
            gpu_estimate = int(total_estimate * gpu_offload_ratio)
            host_estimate = total_estimate - gpu_estimate
            gpu_budget = max(0, gpu.free_memory_bytes - policy.gpu_reserve_bytes)
            if host_estimate <= host_budget and gpu_estimate <= gpu_budget:
                placement = "gpu" if gpu_offload_ratio == 1 else "hybrid"
                choices.append({
                    "placement": placement,
                    "gpu_name": gpu.name,
                    "gpu_vendor": gpu.vendor,
                    "gpu_offload_ratio": float(gpu_offload_ratio),
                    "estimated_host_memory_bytes": host_estimate,
                    "estimated_gpu_memory_bytes": gpu_estimate,
                    "feasible": True,
                    "reason": "host_and_gpu_memory_budgets_satisfied",
                })

    return {
        "model_id": model.name,
        "estimated_total_runtime_memory_bytes": total_estimate,
        "available_host_memory_bytes": hardware.available_memory_bytes,
        "usable_host_memory_bytes": host_budget,
        "choices": choices,
        "recommended_placement": choices[0]["placement"] if choices else None,
        "fit": bool(choices),
        "estimate_kind": "installed_size_with_runtime_and_safety_margins",
        "confidence": "low",
        "requires_runtime_measurement": True,
        "automatic_activation": False,
        "gpu_telemetry_required": True,
        "notes": [
            "Available memory is a point-in-time snapshot and may change before model load.",
            "GPU placement is considered only when an explicit offload ratio and free-VRAM telemetry are supplied.",
            "Quantization is reflected only indirectly in the installed artifact size.",
            "Context length, KV cache, backend workspace, and fragmentation may increase actual memory use.",
        ],
    }
