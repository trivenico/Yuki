"""Read-only, cross-platform host hardware snapshot.

GPU memory is intentionally not inferred. This module performs no subprocess,
network, provider, model-download, or device-activation operations.
"""
from __future__ import annotations

import os
import platform
from typing import Optional


def _positive_sysconf(name: str) -> Optional[int]:
    try:
        value = os.sysconf(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
    except (AttributeError, OSError, ValueError):
        return None


def _posix_memory():
    page_size = _positive_sysconf("SC_PAGE_SIZE")
    total_pages = _positive_sysconf("SC_PHYS_PAGES")
    available_pages = _positive_sysconf("SC_AVPHYS_PAGES")
    total = page_size * total_pages if page_size and total_pages else None
    available = page_size * available_pages if page_size and available_pages else None
    if total is not None and available is not None:
        available = min(available, total)
    return total, available


def _windows_memory():
    if os.name != "nt":
        return None, None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
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
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None, None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except (AttributeError, OSError, ImportError):
        return None, None


def detect_hardware():
    """Return a privacy-conscious hardware snapshot with explicit unknowns."""
    system = platform.system() or "unknown"
    architecture = platform.machine() or "unknown"
    logical_cpus = os.cpu_count()
    if isinstance(logical_cpus, bool) or not isinstance(logical_cpus, int) or logical_cpus < 1:
        logical_cpus = None

    if os.name == "nt":
        total_memory, available_memory = _windows_memory()
        memory_source = "windows_global_memory_status_ex" if total_memory else "unavailable"
    elif os.name == "posix":
        total_memory, available_memory = _posix_memory()
        memory_source = "posix_sysconf" if total_memory else "unavailable"
    else:
        total_memory, available_memory = None, None
        memory_source = "unavailable"

    return {
        "operating_system": system,
        "architecture": architecture,
        "logical_cpu_count": logical_cpus,
        "total_memory_bytes": total_memory,
        "available_memory_bytes": available_memory,
        "memory_source": memory_source,
        "gpu": {"detected": None, "memory_bytes": None, "status": "not_probed"},
        "collection": "local_read_only",
        "network_access": False,
        "automatic_model_download": False,
        "automatic_model_activation": False,
    }
