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


def build_readiness_report(
    *,
    environ: dict[str, str] | None = None,
    executable_finder: Callable[[str], str | None] = shutil.which,
    module_finder: Callable[[str], object] = importlib.util.find_spec,
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
