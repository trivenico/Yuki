"""Portable local-device readiness and model admission guidance.

This is an advisory, read-only planner. It does not install packages, inspect
GPU drivers, download models, start runtimes, or guarantee model compatibility.
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping

from providers.device_execution_profile import build_execution_profile
from providers.hardware_snapshot import detect_hardware


_GIB = 1024 ** 3
_MAX_BYTES = 2 ** 60
_ALLOWED_OS = frozenset({"Windows", "Linux", "Darwin"})
_ALLOWED_ARCH = frozenset({"x86_64", "AMD64", "aarch64", "arm64"})


class LocalDeviceReadinessError(ValueError):
    """Invalid local-device path or model requirement manifest."""


def _bounded_bytes(value: Any, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= _MAX_BYTES:
        raise LocalDeviceReadinessError("invalid_" + field)
    return value


def _model_compatibility(
    model: Mapping[str, Any],
    *,
    os_name: str,
    architecture: str,
    runtime_budget: int | None,
    free_disk: int,
) -> dict[str, Any]:
    allowed = {
        "model_id", "minimum_ram_bytes", "recommended_ram_bytes",
        "download_bytes", "minimum_free_disk_bytes",
        "supported_operating_systems", "supported_architectures",
        "requires_gpu",
    }
    if not isinstance(model, Mapping) or set(model) - allowed:
        raise LocalDeviceReadinessError("unsupported_model_requirement_fields")
    model_id = model.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 128:
        raise LocalDeviceReadinessError("invalid_model_id")
    minimum_ram = _bounded_bytes(model.get("minimum_ram_bytes"), "minimum_ram_bytes")
    recommended_ram = _bounded_bytes(model.get("recommended_ram_bytes"), "recommended_ram_bytes")
    download_bytes = _bounded_bytes(model.get("download_bytes"), "download_bytes", allow_zero=True)
    minimum_disk = _bounded_bytes(
        model.get("minimum_free_disk_bytes"), "minimum_free_disk_bytes", allow_zero=True
    )
    if recommended_ram < minimum_ram:
        raise LocalDeviceReadinessError("recommended_ram_below_minimum")
    systems = model.get("supported_operating_systems")
    architectures = model.get("supported_architectures")
    if (not isinstance(systems, (list, tuple)) or not systems
            or any(item not in _ALLOWED_OS for item in systems)):
        raise LocalDeviceReadinessError("invalid_supported_operating_systems")
    if (not isinstance(architectures, (list, tuple)) or not architectures
            or any(item not in _ALLOWED_ARCH for item in architectures)):
        raise LocalDeviceReadinessError("invalid_supported_architectures")
    requires_gpu = model.get("requires_gpu", False)
    if type(requires_gpu) is not bool:
        raise LocalDeviceReadinessError("invalid_requires_gpu")

    reasons = []
    if os_name not in systems:
        reasons.append("unsupported_operating_system")
    if architecture not in architectures:
        reasons.append("unsupported_cpu_architecture")
    if runtime_budget is None or runtime_budget < minimum_ram:
        reasons.append("insufficient_advisory_ram_budget")
    if free_disk < max(download_bytes, minimum_disk):
        reasons.append("insufficient_free_disk")
    if requires_gpu:
        reasons.append("gpu_requirement_not_verified")

    compatible = not reasons
    recommended = compatible and runtime_budget is not None and runtime_budget >= recommended_ram
    return {
        "model_id": model_id,
        "status": "recommended" if recommended else "fits_minimum" if compatible else "not_admitted",
        "compatible": compatible,
        "recommended": recommended,
        "download_bytes": download_bytes,
        "free_disk_bytes": free_disk,
        "advisory_runtime_memory_budget_bytes": runtime_budget,
        "reasons": reasons,
        "limitations": [
            "Compatibility is advisory and must be confirmed by the selected inference runtime.",
            "GPU requirements are not admitted until a trusted GPU probe is available.",
            "Actual memory use depends on quantization, context length, KV cache and runtime.",
        ],
    }


def build_local_device_report(
    model_directory: str | os.PathLike[str],
    *,
    model_requirements: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Inspect a local machine and score caller-supplied model requirements."""
    target = Path(model_directory).expanduser()
    try:
        if not target.is_dir():
            raise LocalDeviceReadinessError("model_directory_must_exist")
        disk = shutil.disk_usage(target)
    except OSError as exc:
        raise LocalDeviceReadinessError("model_directory_unavailable") from exc

    hardware = detect_hardware()
    profile = None
    profile_error = None
    if hardware.get("total_memory_bytes") and hardware.get("available_memory_bytes"):
        try:
            profile = build_execution_profile(hardware)
        except (TypeError, ValueError) as exc:
            profile_error = str(exc)

    os_name = hardware.get("operating_system") or platform.system() or "unknown"
    architecture = hardware.get("architecture") or platform.machine() or "unknown"
    runtime_budget = (
        profile.get("model_runtime_memory_budget_bytes") if profile else None
    )
    requirements = list(model_requirements)
    if len(requirements) > 256:
        raise LocalDeviceReadinessError("too_many_model_requirements")
    models = [
        _model_compatibility(
            item,
            os_name=os_name,
            architecture=architecture,
            runtime_budget=runtime_budget,
            free_disk=disk.free,
        )
        for item in requirements
    ]
    return {
        "schema_version": 1,
        "device": {
            "operating_system": os_name,
            "architecture": architecture,
            "logical_cpu_count": hardware.get("logical_cpu_count"),
            "total_memory_bytes": hardware.get("total_memory_bytes"),
            "available_memory_bytes": hardware.get("available_memory_bytes"),
            "gpu_status": "not_probed",
        },
        "storage": {
            "model_directory": str(target.resolve()),
            "free_bytes": disk.free,
            "total_bytes": disk.total,
        },
        "execution_profile": profile,
        "execution_profile_error": profile_error,
        "model_recommendations": models,
        "default_backend_policy": "cpu_first_gpu_only_after_verified_probe",
        "automatic_download": False,
        "automatic_install": False,
        "automatic_runtime_start": False,
        "limitations": [
            "GPU detection and driver compatibility are not implemented in this report.",
            "Model recommendations require a trusted, versioned model requirements catalog.",
            "A local runtime must perform its own load-time memory and compatibility checks.",
        ],
    }
