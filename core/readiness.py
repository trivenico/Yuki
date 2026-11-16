"""Read-only runtime capability inventory for operator diagnostics.

This module detects capabilities without installing packages, starting services,
or changing machine configuration. Detection is not a guarantee that a feature
works; the report intentionally distinguishes availability from verification.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import os
import platform
import shutil
import sys
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from dataclasses import asdict, dataclass
from pathlib import Path

from core.component_catalog import component_catalog, validate_component_catalog
from core.component_registry import ComponentRegistry
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
        # Require an IP literal, not "localhost": resolver configuration can
        # redirect hostnames, and urllib's default opener can honor proxy env vars.
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return Capability("unavailable", "Ollama readiness requires a literal loopback IP address.")
        if not address.is_loopback:
            return Capability("unavailable", "Only loopback Ollama endpoints are allowed for local readiness checks.")
        endpoint = base_url.rstrip("/") + "/api/tags"
        request = Request(endpoint, headers={"Accept": "application/json"}, method="GET")
        # Never route a local health probe through HTTP(S)_PROXY settings.
        open_url = opener if opener is not None else build_opener(ProxyHandler({})).open
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
    component_registry: ComponentRegistry | None = None,
    repository_root: str | Path | None = None,
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
    # The canonical component catalog is a read-only architecture contract.
    # Report its validation separately from runtime feature detection.
    catalog = component_catalog()
    catalog_root = Path(repository_root).resolve() if repository_root is not None else Path(__file__).resolve().parents[1]
    # Source-file evidence is intentionally excluded from published wheels.
    # Do not misreport an installed distribution as a broken architecture
    # when the repository's test/evidence tree is simply not shipped.
    if not (catalog_root / "tests" / "test_component_catalog.py").is_file():
        capabilities["architecture_catalog"] = Capability(
            "not_verified",
            "Component contracts are bundled, but repository source/test evidence is not installed; run validation from a source checkout.",
        )
    else:
        catalog_errors = validate_component_catalog(
            catalog, repository_root=catalog_root
        )
        capabilities["architecture_catalog"] = (
            Capability(
                "verified",
                f"{len(catalog)} component contracts and dependency references validated.",
            )
            if not catalog_errors
            else Capability(
                "unavailable",
                f"Component catalog validation failed ({len(catalog_errors)} issue(s)).",
            )
        )

    if component_registry is None:
        capabilities["component_runtime"] = Capability(
            "unconfigured", "No component runtime registry was supplied; runtime health was not checked."
        )
    else:
        try:
            started = component_registry.started_components
            health = component_registry.health()
            if not started:
                capabilities["component_runtime"] = Capability(
                    "unavailable", "A registry was supplied but no components are started."
                )
            elif all(health.get(component_id) is True for component_id in started):
                capabilities["component_runtime"] = Capability(
                    "verified", f"All {len(started)} started component runtime(s) report healthy."
                )
            else:
                capabilities["component_runtime"] = Capability(
                    "unavailable", "One or more started component runtimes are unhealthy or missing health evidence."
                )
        except Exception as exc:
            capabilities["component_runtime"] = Capability(
                "unavailable", f"Component runtime health check failed ({type(exc).__name__})."
            )

    return ReadinessReport(
        python=platform.python_version(),
        implementation=platform.python_implementation(),
        operating_system=platform.system(),
        architecture=platform.machine(),
        capabilities=capabilities,
    )
