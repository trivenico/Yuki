"""Cross-platform, read-only hardware inventory and model resource estimates.

No shell commands, network probes, driver installation, model downloads, or
hardware changes are performed. GPU details can be supplied by a separately
reviewed runtime-specific probe.
"""
from __future__ import annotations

import os
import platform
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class GPUDevice:
    name: str
    vendor: str | None = None
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = None
    backend: str | None = None


@dataclass(frozen=True)
class HardwareInventory:
    operating_system: str
    os_release: str
    architecture: str
    processor: str
    logical_cpu_count: int
    physical_memory_bytes: int | None
    gpus: tuple[GPUDevice, ...]
    storage_path: str | None = None
    storage_free_bytes: int | None = None
    inventory_scope: str = "read_only_local_inventory"
    gpu_detection: str = "runtime_probe_not_configured"


@dataclass(frozen=True)
class ModelResourceEstimate:
    model_parameters: int
    quantization_bits: float
    weight_bytes_estimate: int
    kv_cache_bytes_estimate: int | None
    total_memory_bytes_estimate: int | None
    disk_bytes_estimate: int
    confidence: str
    assumptions: tuple[str, ...]


def _positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _physical_memory_bytes():
    """Return physical RAM when available, without adding a dependency."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return _positive_int(status.ullTotalPhys)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                return pages * page_size
        except (OSError, ValueError):
            pass
    return None


class HardwareDiscovery:
    """Collect a bounded local inventory; optional GPU probe is dependency-injected."""

    def __init__(self, *, gpu_probe: Callable[[], Sequence[GPUDevice]] | None = None):
        if gpu_probe is not None and not callable(gpu_probe):
            raise TypeError("gpu_probe must be callable")
        self._gpu_probe = gpu_probe

    def discover(self, *, storage_path: str | os.PathLike | None = None) -> HardwareInventory:
        gpus: tuple[GPUDevice, ...] = ()
        gpu_status = "not_detected"
        if self._gpu_probe is not None:
            try:
                result = self._gpu_probe()
                if not isinstance(result, (list, tuple)) or len(result) > 16:
                    raise ValueError("GPU probe returned an invalid device list")
                if any(not isinstance(item, GPUDevice) for item in result):
                    raise ValueError("GPU probe must return GPUDevice records")
                gpus = tuple(result)
                gpu_status = "runtime_probe"
            except Exception:
                # Hardware inventory must remain useful if an optional driver
                # probe fails; never expose exception text from external probes.
                gpu_status = "runtime_probe_failed"

        path_text = None
        free_bytes = None
        if storage_path is not None:
            path = Path(storage_path).expanduser().resolve(strict=True)
            if not path.is_dir():
                raise ValueError("storage_path must identify an existing directory")
            usage = shutil.disk_usage(path)
            path_text = str(path)
            free_bytes = usage.free

        return HardwareInventory(
            operating_system=platform.system() or "unknown",
            os_release=platform.release() or "unknown",
            architecture=platform.machine() or "unknown",
            processor=platform.processor() or "unknown",
            logical_cpu_count=max(1, os.cpu_count() or 1),
            physical_memory_bytes=_physical_memory_bytes(),
            gpus=gpus,
            storage_path=path_text,
            storage_free_bytes=free_bytes,
            gpu_detection=gpu_status,
        )


def estimate_model_resources(
    *,
    model_parameters: int,
    quantization_bits: float,
    context_tokens: int | None = None,
    layers: int | None = None,
    kv_heads: int | None = None,
    head_dimension: int | None = None,
    kv_bytes_per_element: int = 2,
    overhead_ratio: float = 1.15,
) -> ModelResourceEstimate:
    """Estimate model weights and optional KV cache from explicit metadata.

    Estimates are planning aids, not a guarantee of fit. Weight estimates omit
    architecture-specific tensor metadata, runtime workspaces and fragmentation.
    """
    if not isinstance(model_parameters, int) or isinstance(model_parameters, bool) or model_parameters <= 0:
        raise ValueError("model_parameters must be a positive integer")
    if isinstance(quantization_bits, bool) or not isinstance(quantization_bits, (int, float)) or not 1 <= quantization_bits <= 64:
        raise ValueError("quantization_bits must be between 1 and 64")
    if isinstance(overhead_ratio, bool) or not isinstance(overhead_ratio, (int, float)) or not 1 <= overhead_ratio <= 3:
        raise ValueError("overhead_ratio must be between 1 and 3")

    weight_bytes = int(model_parameters * (float(quantization_bits) / 8) * float(overhead_ratio))
    kv_values = (context_tokens, layers, kv_heads, head_dimension)
    kv_present = [value is not None for value in kv_values]
    if any(kv_present) and not all(kv_present):
        raise ValueError("KV cache metadata must be supplied all together")
    if all(kv_present):
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in kv_values):
            raise ValueError("KV cache dimensions must be positive integers")
        if not isinstance(kv_bytes_per_element, int) or isinstance(kv_bytes_per_element, bool) or kv_bytes_per_element not in (1, 2, 4, 8):
            raise ValueError("kv_bytes_per_element must be 1, 2, 4, or 8")
        kv_bytes = 2 * context_tokens * layers * kv_heads * head_dimension * kv_bytes_per_element
        total = weight_bytes + kv_bytes
        confidence = "medium"
        assumptions = (
            "Weight estimate uses parameter_count × quantization_bits / 8 × overhead_ratio.",
            "KV cache assumes standard key/value tensors and the supplied dimensions.",
            "Runtime workspaces, allocator fragmentation, embeddings and architecture-specific metadata are excluded.",
        )
    else:
        kv_bytes = None
        total = None
        confidence = "low"
        assumptions = (
            "Weight estimate uses parameter_count × quantization_bits / 8 × overhead_ratio.",
            "KV cache and runtime workspace are unknown because architecture/context metadata was not supplied.",
            "Runtime workspaces, allocator fragmentation and architecture-specific metadata are excluded.",
        )
    return ModelResourceEstimate(
        model_parameters=model_parameters,
        quantization_bits=float(quantization_bits),
        weight_bytes_estimate=weight_bytes,
        kv_cache_bytes_estimate=kv_bytes,
        total_memory_bytes_estimate=total,
        disk_bytes_estimate=weight_bytes,
        confidence=confidence,
        assumptions=assumptions,
    )


def inventory_to_dict(inventory: HardwareInventory) -> dict:
    """Return JSON-serializable inventory without machine identifiers."""
    return asdict(inventory)
