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
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class Capability:
    status: str
    detail: str


@dataclass(frozen=True)
class ResourceInventory:
    """Best-effort host resource facts; values are observations, not capacity guarantees."""

    logical_cpu_count: int | None
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    memory_source: str
    memory_cgroup_limit_bytes: int | None
    memory_cgroup_available_bytes: int | None
    memory_cgroup_source: str

    @classmethod
    def detect(
        cls,
        *,
        meminfo_reader: Callable[[], str] | None = None,
        cpu_counter: Callable[[], int | None] = os.cpu_count,
        operating_system: str | None = None,
        cgroup_reader: Callable[[str], str | None] | None = None,
    ) -> "ResourceInventory":
        system = platform.system() if operating_system is None else operating_system
        cpu_count = cpu_counter()
        if not isinstance(cpu_count, int) or cpu_count < 1:
            cpu_count = None

        total = available = None
        source = "unavailable"
        if system == "Linux":
            try:
                read_meminfo = meminfo_reader or (lambda: open("/proc/meminfo", encoding="ascii").read())
                values: dict[str, int] = {}
                for line in read_meminfo().splitlines():
                    key, separator, raw_value = line.partition(":")
                    if not separator:
                        continue
                    parts = raw_value.strip().split()
                    if len(parts) >= 2 and parts[1] == "kB":
                        try:
                            values[key] = int(parts[0]) * 1024
                        except ValueError:
                            continue
                total = values.get("MemTotal")
                available = values.get("MemAvailable")
                source = "linux_proc_meminfo" if total is not None or available is not None else "unavailable"
            except (OSError, UnicodeError, ValueError):
                pass

        cgroup_limit = cgroup_available = None
        cgroup_source = "unavailable"
        if system == "Linux":
            def read_cgroup(path: str) -> str | None:
                if cgroup_reader is not None:
                    return cgroup_reader(path)
                try:
                    return Path(path).read_text(encoding="ascii").strip()
                except (OSError, UnicodeError):
                    return None

            # Prefer cgroup v2. Fall back to v1 when v2 files are absent or
            # report "max". A v1 near-integer-limit value means unlimited.
            v2_limit = read_cgroup("/sys/fs/cgroup/memory.max")
            v2_current = read_cgroup("/sys/fs/cgroup/memory.current")
            if v2_limit is not None and v2_current is not None and v2_limit.strip() != "max":
                try:
                    parsed_limit = int(v2_limit.strip())
                    parsed_current = int(v2_current.strip())
                    if parsed_limit > 0 and parsed_current >= 0:
                        cgroup_limit = parsed_limit
                        cgroup_available = max(0, parsed_limit - parsed_current)
                        cgroup_source = "cgroup_v2"
                except ValueError:
                    pass

            if cgroup_limit is None:
                v1_limit = read_cgroup("/sys/fs/cgroup/memory/memory.limit_in_bytes")
                v1_usage = read_cgroup("/sys/fs/cgroup/memory/memory.usage_in_bytes")
                if v1_limit is not None and v1_usage is not None:
                    try:
                        parsed_limit = int(v1_limit.strip())
                        parsed_usage = int(v1_usage.strip())
                        # Linux cgroup v1 represents "unlimited" with a very
                        # large sentinel rather than the literal word "max".
                        if 0 < parsed_limit < (1 << 60) and parsed_usage >= 0:
                            cgroup_limit = parsed_limit
                            cgroup_available = max(0, parsed_limit - parsed_usage)
                            cgroup_source = "cgroup_v1"
                    except ValueError:
                        pass

        return cls(
            logical_cpu_count=cpu_count,
            memory_total_bytes=total,
            memory_available_bytes=available,
            memory_source=source,
            memory_cgroup_limit_bytes=cgroup_limit,
            memory_cgroup_available_bytes=cgroup_available,
            memory_cgroup_source=cgroup_source,
        )


@dataclass(frozen=True)
class ReadinessReport:
    python: str
    implementation: str
    operating_system: str
    architecture: str
    resources: ResourceInventory
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


def build_readiness_report(
    *,
    environ: dict[str, str] | None = None,
    executable_finder: Callable[[str], str | None] = shutil.which,
    module_finder: Callable[[str], object] = importlib.util.find_spec,
    ollama_probe: Callable[[str, float], Capability] = _probe_ollama,
) -> ReadinessReport:
    """Build a deterministic, side-effect-free report of locally detectable features."""
    env = os.environ if environ is None else environ
    capabilities: dict[str, Capability] = {
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
        resources=ResourceInventory.detect(),
        capabilities=capabilities,
    )
