"""Read-only runtime capability inventory for operator diagnostics.

This module detects capabilities without installing packages, starting services,
or changing machine configuration. Detection is not a guarantee that a feature
works; the report intentionally distinguishes availability from verification.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from dataclasses import asdict, dataclass
from typing import Callable

import ctypes
import subprocess
from pathlib import Path


def _total_memory_bytes() -> int | None:
    """Return physical RAM using platform facilities; never shell out for this value."""
    try:
        if sys.platform == "win32":
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
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
            return None
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                return pages * page_size
    except (AttributeError, OSError, ValueError):
        return None
    return None


def _available_memory_bytes() -> int | None:
    """Return currently available physical RAM without installing dependencies."""
    try:
        if sys.platform == "win32":
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
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
            return None
        if sys.platform.startswith("linux"):
            # MemAvailable accounts for reclaimable cache, unlike MemFree.
            with open("/proc/meminfo", encoding="ascii") as info:
                for line in info:
                    if line.startswith("MemAvailable:"):
                        fields = line.split()
                        if len(fields) >= 2 and fields[1].isdigit():
                            return int(fields[1]) * 1024
            return None
        try:
            import psutil
            return int(psutil.virtual_memory().available)
        except (ImportError, AttributeError, OSError, ValueError):
            return None
    except (AttributeError, OSError, ValueError):
        return None


def _detect_gpu() -> Capability:
    """Detect NVIDIA via its read-only CLI; report other accelerators as unknown."""
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            result = subprocess.run(
                [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=False,
                shell=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                first = result.stdout.strip().splitlines()[0]
                parts = [part.strip() for part in first.split(",", 1)]
                if len(parts) == 2 and parts[0]:
                    return Capability("detected", f"NVIDIA GPU detected: {parts[0]} ({parts[1]} MiB VRAM).")
        except (OSError, subprocess.TimeoutExpired):
            pass
    if platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        return Capability("possible", "Apple Silicon detected; unified-memory GPU capability not benchmarked.")
    return Capability("unknown", "No supported GPU probe succeeded; CPU fallback remains available.")


def _worker_recommendation(cpu_count: int, memory_bytes: int | None) -> tuple[int, str]:
    cores = max(1, int(cpu_count or 1))
    if memory_bytes is None:
        return 1, "Memory could not be measured; use one worker conservatively."
    gib = memory_bytes / (1024 ** 3)
    if gib < 8:
        return 1, "Under 8 GiB RAM: one active specialist worker; prefer small quantized models or remote inference."
    if gib < 16:
        return 1, "8–16 GiB RAM: one active worker; prefer a single 3B–7B quantized model and a bounded context."
    if gib < 32:
        return min(2, cores), "16–32 GiB RAM: at most two active workers; 7B–14B quantized models may fit depending on GPU/context."
    return min(4, cores), "32+ GiB RAM: at most four active workers; larger quantized models may fit, subject to VRAM and context."


@dataclass(frozen=True)
class Capability:
    status: str
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    python: str
    implementation: str
    operating_system: str
    architecture: str
    capabilities: dict[str, Capability]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["capabilities"] = {
            name: asdict(capability)
            for name, capability in self.capabilities.items()
        }
        return data


def _module_available(name: str, finder: Callable = importlib.util.find_spec) -> bool:
    try:
        return finder(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _probe_ollama(base_url: str, timeout: float = 1.0, opener: Callable | None = None) -> Capability:
    """Probe Ollama's local tags endpoint with a strict timeout and no credentials."""
    if not 0 < timeout <= 3:
        return Capability("unavailable", "Probe timeout must be greater than 0 and at most 3 seconds.")
    try:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return Capability("unavailable", "Ollama endpoint must be an HTTP(S) URL with a hostname.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return Capability("unavailable", "Ollama endpoint must not contain credentials, query, or fragment.")
        # This diagnostic is explicitly for a local model server. Do not let an
        # environment variable turn a readiness check into a remote network probe.
        if parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
            return Capability("unavailable", "Only loopback Ollama endpoints are allowed for local readiness checks.")
        endpoint = base_url.rstrip("/") + "/api/tags"
        request = Request(endpoint, headers={"Accept": "application/json"}, method="GET")
        open_url = urlopen if opener is None else opener
        with open_url(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                return Capability("unavailable", f"Ollama endpoint returned HTTP {status}.")
            payload = json.loads(response.read(1_000_001))
        if not isinstance(payload, dict) or not isinstance(payload.get("models", []), list):
            return Capability("unavailable", "Ollama endpoint returned an invalid model-list response.")
        return Capability("verified", "Ollama tags endpoint responded successfully; inference was not tested.")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        # Do not expose endpoint paths, response bodies, or credentials in diagnostics.
        return Capability("unavailable", f"Ollama endpoint probe failed ({type(exc).__name__}).")



def recommended_worker_concurrency(*, available_memory_bytes: int | None = None) -> int:
    """Return a conservative worker count using currently available RAM."""
    available = _available_memory_bytes() if available_memory_bytes is None else available_memory_bytes
    return _worker_recommendation(os.cpu_count() or 1, available)[0]

def build_readiness_report(
    *,
    environ: dict[str, str] | None = None,
    executable_finder: Callable[[str], str | None] = shutil.which,
    module_finder: Callable[[str], object] = importlib.util.find_spec,
    ollama_probe: Callable[[str, float], Capability] = _probe_ollama,
    workspace_path: str | os.PathLike | None = None,
    total_memory_probe: Callable[[], int | None] = _total_memory_bytes,
    available_memory_probe: Callable[[], int | None] = _available_memory_bytes,
) -> ReadinessReport:
    """Build a deterministic, side-effect-free report of locally detectable features."""
    env = os.environ if environ is None else environ
    memory_bytes = total_memory_probe()
    available_memory_bytes = available_memory_probe()
    cpu_count = os.cpu_count() or 1
    workspace = Path(workspace_path or env.get("YUKI_WORKSPACE", Path.cwd())).expanduser()
    try:
        disk_free = shutil.disk_usage(workspace if workspace.exists() else workspace.parent).free
        disk_capability = Capability("detected", f"{disk_free // (1024 ** 3)} GiB free at workspace volume.")
    except OSError:
        disk_free = None
        disk_capability = Capability("unknown", "Workspace volume free space could not be measured.")
    workers, worker_guidance = _worker_recommendation(cpu_count, available_memory_bytes)
    capabilities: dict[str, Capability] = {
        "cpu": Capability("detected", f"{cpu_count} logical CPU(s); architecture {platform.machine() or 'unknown'}."),
        "memory": (Capability("detected", f"{memory_bytes / (1024 ** 3):.1f} GiB physical RAM.") if memory_bytes else Capability("unknown", "Physical RAM could not be measured.")),
        "available_memory": (
            Capability("detected", f"{available_memory_bytes / (1024 ** 3):.1f} GiB currently available RAM.")
            if available_memory_bytes else Capability("unknown", "Available physical RAM could not be measured; worker guidance defaults to one.")
        ),
        "gpu": _detect_gpu(),
        "workspace_disk": disk_capability,
        "recommended_worker_concurrency": Capability("recommended", f"{workers} active worker(s). {worker_guidance}"),
        "local_model_guidance": Capability("advisory", worker_guidance + " Actual model fit depends on quantization, context length, runtime overhead, and accelerator memory."),
        "python_runtime": Capability("detected", sys.version.split()[0]),
        "ollama_cli": (
            Capability("detected", "Ollama executable is on PATH; server/model not verified.")
            if executable_finder("ollama")
            else Capability("unavailable", "Ollama executable not found on PATH.")
        ),
        "ollama_server": (
            ollama_probe(str(env.get("YUKI_OLLAMA_BASE_URL", "http://127.0.0.1:11434")).strip(), 1.0)
            if executable_finder("ollama")
            else Capability("unavailable", "Ollama CLI is unavailable; server connectivity was not checked.")
        ),
        "ollama_model_config": (
            Capability("configured", "A local model name is configured; model availability not verified.")
            if str(env.get("YUKI_OLLAMA_MODEL", "")).strip()
            else Capability("unconfigured", "Set YUKI_OLLAMA_MODEL to select a local model.")
        ),
        "pytorch": (
            Capability("detected", "PyTorch is import-discoverable; accelerator availability not verified.")
            if _module_available("torch", module_finder)
            else Capability("unavailable", "PyTorch is not import-discoverable.")
        ),
        "onnxruntime": (
            Capability("detected", "ONNX Runtime is import-discoverable; execution providers not verified.")
            if _module_available("onnxruntime", module_finder)
            else Capability("unavailable", "ONNX Runtime is not import-discoverable.")
        ),
    }
    return ReadinessReport(
        python=platform.python_version(),
        implementation=platform.python_implementation(),
        operating_system=platform.system(),
        architecture=platform.machine(),
        capabilities=capabilities,
    )
