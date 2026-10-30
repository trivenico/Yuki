"""Privacy-preserving, read-only host hardware inventory for model planning."""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from pathlib import Path


def _memory_bytes():
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys), int(status.ullAvailPhys)
    if hasattr(os, "sysconf"):
        try:
            page = int(os.sysconf("SC_PAGE_SIZE"))
            total = page * int(os.sysconf("SC_PHYS_PAGES"))
            available = page * int(os.sysconf("SC_AVPHYS_PAGES"))
            if total > 0 and available >= 0:
                return total, available
        except (OSError, ValueError, TypeError):
            pass
    try:
        import psutil
        memory = psutil.virtual_memory()
        return int(memory.total), int(memory.available)
    except (ImportError, OSError, AttributeError):
        return None, None


def _nvidia_gpus():
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode != 0:
            return []
        devices = []
        for line in result.stdout.splitlines()[:16]:
            fields = [part.strip() for part in line.split(",")]
            if len(fields) != 3:
                continue
            try:
                memory_mib = int(fields[1])
            except ValueError:
                continue
            if memory_mib <= 0:
                continue
            devices.append({
                "name": fields[0][:120],
                "memory_total_bytes": memory_mib * 1024 * 1024,
                "backend": "cuda",
                "driver_version": fields[2][:40],
            })
        return devices
    except (OSError, subprocess.SubprocessError):
        return []


def _torch_accelerators():
    try:
        import torch
    except (ImportError, OSError):
        return []
    devices = []
    try:
        if torch.cuda.is_available():
            for index in range(min(torch.cuda.device_count(), 16)):
                props = torch.cuda.get_device_properties(index)
                devices.append({
                    "name": str(props.name)[:120],
                    "memory_total_bytes": int(props.total_memory),
                    "backend": "cuda",
                    "driver_version": None,
                })
    except (RuntimeError, AttributeError, OSError):
        pass
    if not devices:
        try:
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                devices.append({
                    "name": "Apple Metal (MPS)",
                    "memory_total_bytes": None,
                    "backend": "mps",
                    "driver_version": None,
                })
        except (RuntimeError, AttributeError):
            pass
    return devices


def detect_hardware(*, model_cache_path=None):
    """Return sanitized hardware facts; no network calls or model loading occur."""
    total, available = _memory_bytes()
    try:
        disk_path = Path(model_cache_path or Path.home()).expanduser()
        disk = shutil.disk_usage(disk_path)
        disk_free = int(disk.free)
        disk_total = int(disk.total)
    except OSError:
        disk_free = disk_total = None

    gpus = _nvidia_gpus()
    if not gpus:
        gpus = _torch_accelerators()
    return {
        "schema_version": 1,
        "platform": platform.system().lower() or "unknown",
        "architecture": platform.machine().lower() or "unknown",
        "logical_cpu_count": os.cpu_count(),
        "ram_total_bytes": total,
        "ram_available_bytes": available,
        "disk_total_bytes": disk_total,
        "disk_free_bytes": disk_free,
        "accelerators": gpus,
        "detection_sources": {
            "memory": "os_or_psutil",
            "gpu": "nvidia_smi_or_optional_torch",
        },
        "network_scanned": False,
        "model_loaded": False,
    }
