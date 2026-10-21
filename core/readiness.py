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
        capabilities=capabilities,
    )
