"""Hardware-aware local model recommendations and Ollama inventory.

This module is intentionally side-effect-light: it never downloads models,
starts services, or contacts non-loopback hosts. Installation requires a
separate, explicit user-approved workflow.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HardwareProfile:
    ram_total_bytes: Optional[int]
    ram_available_bytes: Optional[int]
    disk_free_bytes: Optional[int]
    cpu_count: int
    gpu_name: Optional[str] = None
    gpu_vram_bytes: Optional[int] = None

    @property
    def ram_total_gib(self):
        return round(self.ram_total_bytes / (1024 ** 3), 2) if self.ram_total_bytes else None


@dataclass(frozen=True)
class ModelRecommendation:
    model: str
    estimated_ram_gib: float
    reason: str
    confidence: str


def detect_hardware(*, workspace="."):
    """Detect basic host resources without requiring optional dependencies."""
    ram_total = ram_available = None
    try:
        import psutil  # optional
        memory = psutil.virtual_memory()
        ram_total, ram_available = int(memory.total), int(memory.available)
    except ImportError:
        if Path("/proc/meminfo").exists():
            values = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0]) * 1024
            ram_total = values.get("MemTotal")
            ram_available = values.get("MemAvailable")
    disk_free = None
    try:
        disk_free = shutil.disk_usage(Path(workspace).expanduser()).free
    except OSError:
        pass
    gpu_name = None
    gpu_vram = None
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            output = subprocess.run(
                [executable, "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                check=True, capture_output=True, text=True, timeout=3,
            ).stdout.splitlines()[0]
            gpu_name, memory_mib = [part.strip() for part in output.split(",", 1)]
            gpu_vram = int(memory_mib) * 1024 ** 2
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            gpu_name = gpu_vram = None
    return HardwareProfile(ram_total, ram_available, disk_free,
                           max(1, __import__("os").cpu_count() or 1),
                           gpu_name, gpu_vram)


def recommend_models(profile: HardwareProfile):
    """Conservative starter recommendations, not benchmark guarantees."""
    usable = profile.ram_available_bytes or profile.ram_total_bytes
    if not usable:
        return [ModelRecommendation("qwen3:0.6b", 1.5,
                                    "Memory could not be detected; use a small starter model.",
                                    "low")]
    # System-available RAM is the conservative baseline. VRAM is reported
    # separately; it must not mask insufficient host memory.
    gib = usable / 1024 ** 3
    if gib < 4:
        choices = [("qwen3:0.6b", 1.5), ("gemma3:1b", 2.0)]
    elif gib < 8:
        choices = [("qwen3:1.7b", 3.5), ("gemma3:4b", 5.0)]
    elif gib < 16:
        choices = [("qwen3:4b", 6.0), ("gemma3:4b", 5.0)]
    else:
        choices = [("qwen3:8b", 10.0), ("qwen3:4b", 6.0)]
    return [ModelRecommendation(name, estimate,
             "Conservative estimate based on detected available memory; validate with a real benchmark.",
             "medium") for name, estimate in choices]


class OllamaLocalClient:
    """Small local-only Ollama inventory client; never silently downloads."""

    def __init__(self, base_url="http://127.0.0.1:11434", timeout=3):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Ollama inventory is restricted to a loopback HTTP endpoint.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path):
        request = urllib.request.Request(self.base_url + path, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"Ollama returned HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise ConnectionError("Local Ollama is unavailable.") from exc

    def status(self):
        try:
            payload = self._get("/api/tags")
        except (ConnectionError, TimeoutError, ValueError):
            return {"available": False, "models": []}
        models = []
        for item in payload.get("models", []):
            models.append({
                "name": item.get("name"),
                "size_bytes": item.get("size"),
                "modified_at": item.get("modified_at"),
                "digest": item.get("digest"),
            })
        return {"available": True, "models": models}


class LocalModelManager:
    def __init__(self, *, workspace=".", ollama_client=None):
        self.workspace = Path(workspace).expanduser()
        self.ollama = ollama_client or OllamaLocalClient()

    def discover_candidates(self, *, task="text-generation", sort="downloads",
                            limit=20, token=None, discovery_client=None):
        """Explicitly query Hub metadata; this never downloads or activates models."""
        from providers.huggingface_discovery import HuggingFaceDiscovery

        client = discovery_client or HuggingFaceDiscovery(max_results=limit)
        result = client.discover(task=task, sort=sort, token=token)
        return {
            **result,
            "hardware_profile": asdict(detect_hardware(workspace=self.workspace)),
            "installation_policy": "explicit_user_approval_required",
            "downloads_performed": False,
        }

    def inspect(self):
        profile = detect_hardware(workspace=self.workspace)
        inventory = self.ollama.status()
        return {
            "hardware": asdict(profile),
            "ollama": inventory,
            "recommendations": [asdict(item) for item in recommend_models(profile)],
            "installation_policy": "explicit_user_approval_required",
            "downloads_performed": False,
        }
