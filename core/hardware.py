"""Cross-platform, read-only hardware inventory for local model selection.

Detection is best-effort and uses the standard library. It does not install drivers,
download models, change settings, or imply that a detected GPU backend is usable by
a particular inference runtime.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


@dataclass(frozen=True)
class HardwareInfo:
    os_name: str
    os_version: str
    architecture: str
    cpu_brand: str
    cpu_count: int
    ram_total_gb: float
    ram_available_gb: float
    gpu_vendor: str | None
    gpu_name: str | None
    gpu_vram_gb: float | None
    gpu_detection_method: str | None
    disk_free_gb: float | None
    inference_runtimes: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        """Return a JSON-serializable inventory."""
        value = asdict(self)
        value["inference_runtimes"] = list(self.inference_runtimes)
        value["warnings"] = list(self.warnings)
        return value


def _parse_linux_memory(text: str) -> tuple[float, float]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, rest = line.partition(":")
        if separator and key in {"MemTotal", "MemAvailable"}:
            try:
                values[key] = int(rest.strip().split()[0])
            except (ValueError, IndexError):
                continue
    total_kib = values.get("MemTotal", 0)
    available_kib = values.get("MemAvailable", total_kib)
    return round(total_kib / 1024**2, 2), round(available_kib / 1024**2, 2)


def _linux_memory() -> tuple[float, float]:
    try:
        return _parse_linux_memory(Path("/proc/meminfo").read_text(encoding="ascii"))
    except (OSError, UnicodeError):
        return 0.0, 0.0


def _windows_memory() -> tuple[float, float]:
    """Read physical memory with GlobalMemoryStatusEx; no psutil dependency."""
    class MemoryStatusEx(ctypes.Structure):
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

    try:
        windll = getattr(ctypes, "windll", None)
        kernel32 = getattr(windll, "kernel32", None)
        memory_status = getattr(kernel32, "GlobalMemoryStatusEx", None)
        if memory_status is None:
            return 0.0, 0.0
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not memory_status(ctypes.byref(status)):
            return 0.0, 0.0
        return (
            round(status.ullTotalPhys / 1024**3, 2),
            round(status.ullAvailPhys / 1024**3, 2),
        )
    except (AttributeError, OSError, TypeError):
        return 0.0, 0.0


def _macos_memory() -> tuple[float, float]:
    """Read total RAM with sysctl and estimate available pages using vm_stat."""
    try:
        total_result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        total_bytes = int(total_result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        total_bytes = 0

    try:
        result = subprocess.run(
            ["vm_stat"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return round(total_bytes / 1024**3, 2), 0.0

    page_match = re.search(r"page size of (\d+) bytes", result.stdout)
    page_size = int(page_match.group(1)) if page_match else 4096
    page_counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            try:
                page_counts[key.strip().lower()] = int(value.strip().rstrip("."))
            except ValueError:
                continue

    available_pages = sum(
        page_counts.get(key, 0)
        for key in ("pages free", "pages inactive", "pages speculative", "pages purgeable")
    )
    available_bytes = available_pages * page_size
    return round(total_bytes / 1024**3, 2), round(available_bytes / 1024**3, 2)


def _memory_info(os_name: str) -> tuple[float, float]:
    if os_name == "linux":
        result = _linux_memory()
    elif os_name == "windows":
        result = _windows_memory()
    elif os_name == "darwin":
        result = _macos_memory()
    else:
        result = (0.0, 0.0)

    if result[0] > 0:
        return result

    # Optional fallback for unusual Linux/container setups and other platforms.
    try:
        import psutil  # type: ignore[import-not-found]

        memory = psutil.virtual_memory()
        return round(memory.total / 1024**3, 2), round(memory.available / 1024**3, 2)
    except (ImportError, OSError, AttributeError):
        return 0.0, 0.0


def _cpu_brand(os_name: str) -> str:
    if os_name == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    _, _, value = line.partition(":")
                    if value.strip():
                        return value.strip()[:160]
        except OSError:
            pass
    if os_name == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.stdout.strip():
                return result.stdout.strip()[:160]
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor()[:160] or platform.machine() or "Unknown CPU"


def _nvidia_gpu() -> tuple[str, float] | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
    name, separator, memory = first.rpartition(",")
    if not separator:
        return None
    try:
        vram_gb = float(memory.strip()) / 1024
    except ValueError:
        return None
    return name.strip()[:160], round(vram_gb, 2)


def detect_hardware(*, workspace: str | os.PathLike[str] | None = None) -> HardwareInfo:
    """Return host capabilities without requiring optional packages or elevation."""
    system = platform.system()
    os_name = system.lower()
    total, available = _memory_info(os_name)
    warnings: list[str] = []
    if total <= 0:
        warnings.append("Physical memory could not be detected; model-fit estimates are unavailable.")

    gpu = _nvidia_gpu()
    gpu_vendor = "nvidia" if gpu else None
    gpu_name = gpu[0] if gpu else None
    gpu_vram = gpu[1] if gpu else None
    gpu_method = "nvidia-smi" if gpu else None

    # On Apple Silicon, identify the integrated-GPU platform without claiming
    # Metal is enabled or supported by the selected inference runtime.
    if not gpu and os_name == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        gpu_vendor = "apple"
        gpu_name = "Apple Silicon integrated GPU (runtime acceleration unverified)"
        gpu_method = "platform heuristic"

    disk_free = None
    try:
        disk_free = round(shutil.disk_usage(Path(workspace or Path.cwd())).free / 1024**3, 2)
    except OSError:
        warnings.append("Free disk space could not be detected.")

    runtimes = tuple(
        name for name in ("ollama", "llama-server", "llama-cli") if shutil.which(name)
    )
    if not gpu:
        warnings.append(
            "No NVIDIA GPU was confirmed. Other GPU vendors and runtime acceleration "
            "require separate backend-specific checks."
        )
    if not runtimes:
        warnings.append("No supported local inference executable was found on PATH.")

    return HardwareInfo(
        os_name=os_name,
        os_version=platform.version()[:160],
        architecture=platform.machine()[:80] or "unknown",
        cpu_brand=_cpu_brand(os_name),
        cpu_count=max(1, os.cpu_count() or 1),
        ram_total_gb=total,
        ram_available_gb=available,
        gpu_vendor=gpu_vendor,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram,
        gpu_detection_method=gpu_method,
        disk_free_gb=disk_free,
        inference_runtimes=runtimes,
        warnings=tuple(warnings),
    )


def recommend_installed_model(models: list[dict], hardware: HardwareInfo) -> str | None:
    """Recommend an installed model using a conservative RAM estimate.

    Model size is only a rough proxy. Context/KV cache, runtime overhead, GPU
    offload, quantization, and concurrent tasks can change actual requirements.
    """
    valid = [
        item for item in models
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].strip()
    ]
    if not valid:
        return None

    available = hardware.ram_available_gb
    if available <= 0 or hardware.ram_total_gb <= 0:
        return None

    reserve_gb = max(2.0, hardware.ram_total_gb * 0.15)
    budget_gb = max(0.0, available - reserve_gb)
    sized: list[tuple[float, str]] = []
    for item in valid:
        name = item["name"].strip()
        size = item.get("size")
        if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
            size_gb = size / 1024**3
            if size_gb * 1.35 <= budget_gb:
                sized.append((size_gb, name))

    if sized:
        return max(sized, key=lambda pair: (pair[0], pair[1].casefold()))[1]
    # Missing size metadata or no model within the estimated budget is not enough
    # evidence to claim that any installed model is a safe fit.
    return None
