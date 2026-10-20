"""Portable hardware inventory and conservative local-model fit estimates.

Detection is read-only: it never downloads models, changes configuration, or runs
arbitrary shell commands. GPU details are best-effort and require vendor tooling.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess


@dataclass(frozen=True)
class HardwareInfo:
    platform: str
    cpu_brand: str
    cpu_count: int
    ram_total_gb: float
    ram_available_gb: float
    gpu_vendor: str | None
    gpu_name: str | None
    gpu_vram_gb: float | None
    disk_free_gb: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def _linux_memory() -> tuple[float, float]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, _, rest = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(rest.strip().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    total = values.get("MemTotal", 0) / 1024 / 1024
    available = values.get("MemAvailable", values.get("MemTotal", 0)) / 1024 / 1024
    return round(total, 2), round(available, 2)


def _cpu_brand() -> str:
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    _, _, value = line.partition(":")
                    if value.strip():
                        return value.strip()[:160]
        except OSError:
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
    """Detect host resources with stdlib/procfs and optional nvidia-smi."""
    if platform.system() == "Linux":
        total, available = _linux_memory()
    else:
        total = available = 0.0
        try:
            import psutil  # type: ignore[import-not-found]

            memory = psutil.virtual_memory()
            total = round(memory.total / 1024**3, 2)
            available = round(memory.available / 1024**3, 2)
        except ImportError:
            pass
    gpu = _nvidia_gpu()
    disk_free = None
    try:
        disk_free = round(shutil.disk_usage(Path(workspace or Path.cwd())).free / 1024**3, 2)
    except OSError:
        pass
    return HardwareInfo(
        platform=platform.system().lower(),
        cpu_brand=_cpu_brand(),
        cpu_count=max(1, os.cpu_count() or 1),
        ram_total_gb=total,
        ram_available_gb=available,
        gpu_vendor="nvidia" if gpu else None,
        gpu_name=gpu[0] if gpu else None,
        gpu_vram_gb=gpu[1] if gpu else None,
        disk_free_gb=disk_free,
    )


def recommend_installed_model(models: list[dict], hardware: HardwareInfo) -> str | None:
    """Choose the largest installed model that fits a conservative RAM budget.

    Ollama model size is a useful first-order proxy, not a guarantee: context
    length, KV cache, concurrent requests, and runtime overhead also consume RAM.
    Models without size metadata are considered only when no sized model fits.
    """
    valid = [
        model
        for model in models
        if isinstance(model, dict)
        and isinstance(model.get("name"), str)
        and model["name"].strip()
    ]
    if not valid:
        return None
    available = hardware.ram_available_gb
    if available <= 0:
        return sorted((m["name"] for m in valid), key=str.casefold)[0]
    reserve_gb = max(2.0, hardware.ram_total_gb * 0.15)
    budget_gb = max(0.0, available - reserve_gb)
    sized = []
    unsized = []
    for model in valid:
        size = model.get("size")
        name = model["name"].strip()
        if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
            size_gb = size / 1024**3
            if size_gb * 1.35 <= budget_gb:
                sized.append((size_gb, name))
        else:
            unsized.append(name)
    if sized:
        return max(sized, key=lambda item: (item[0], item[1].casefold()))[1]
    if unsized:
        return sorted(unsized, key=str.casefold)[0]
    # No model meets the conservative estimate: choose the smallest installed
    # model only as a best-effort fallback, not as a claim it will fit.
    smallest = min(
        (m for m in valid if isinstance(m.get("size"), (int, float)) and not isinstance(m.get("size"), bool)),
        key=lambda item: item["size"],
        default=None,
    )
    return str(smallest["name"]) if smallest else sorted((m["name"] for m in valid), key=str.casefold)[0]
