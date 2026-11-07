"""Provenance-gated local model profiles for portable device admission.

Profiles carry resource and runtime metadata. They are not model weights and do
not establish compatibility by themselves. Admission requires a matching model
catalog entry with reviewed provenance, license and passing evaluation.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from providers.local_device_readiness import _model_compatibility


class LocalModelProfileError(ValueError):
    """Invalid or untrusted local model runtime profile."""


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_LICENSE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+()-]{0,79}$")
_FORMATS = frozenset({"gguf", "safetensors", "onnx", "ollama"})
_QUANTIZATIONS = frozenset({
    "none", "fp16", "bf16", "fp32", "q2_k", "q3_k_s", "q3_k_m",
    "q4_0", "q4_k_s", "q4_k_m", "q5_0", "q5_k_s", "q5_k_m",
    "q6_k", "q8_0", "int8", "int4",
})
_SYSTEMS = frozenset({"Windows", "Linux", "Darwin"})
_ARCHITECTURES = frozenset({"x86_64", "AMD64", "aarch64", "arm64"})
_MAX_BYTES = 2 ** 60


def _positive_bytes(value: Any, field: str, *, zero: bool = False) -> int:
    if type(value) is not int or value < (0 if zero else 1) or value > _MAX_BYTES:
        raise LocalModelProfileError("invalid_" + field)
    return value


def validate_local_model_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate strict, versioned model requirements and provenance metadata."""
    allowed = {
        "schema_version", "model_id", "model_version", "artifact_sha256",
        "source_url", "license_id", "format", "quantization",
        "minimum_ram_bytes", "recommended_ram_bytes", "artifact_bytes",
        "minimum_free_disk_bytes", "supported_operating_systems",
        "supported_architectures", "requires_gpu", "minimum_vram_bytes",
        "context_tokens", "runtime",
    }
    if not isinstance(value, Mapping) or set(value) != allowed:
        raise LocalModelProfileError("profile_fields_must_match_schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise LocalModelProfileError("unsupported_profile_schema")
    model_id, version = value["model_id"], value["model_version"]
    if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
        raise LocalModelProfileError("invalid_model_id")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", version):
        raise LocalModelProfileError("invalid_model_version")
    digest = value["artifact_sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise LocalModelProfileError("invalid_artifact_sha256")
    source_url = value["source_url"]
    if (not isinstance(source_url, str) or len(source_url) > 2048
            or not source_url.startswith("https://") or "@" in source_url.split("/", 3)[2]):
        raise LocalModelProfileError("source_url_must_be_https_without_credentials")
    license_id = value["license_id"]
    if not isinstance(license_id, str) or not _LICENSE.fullmatch(license_id):
        raise LocalModelProfileError("invalid_license_id")
    if value["format"] not in _FORMATS or value["quantization"] not in _QUANTIZATIONS:
        raise LocalModelProfileError("unsupported_format_or_quantization")

    minimum_ram = _positive_bytes(value["minimum_ram_bytes"], "minimum_ram_bytes")
    recommended_ram = _positive_bytes(value["recommended_ram_bytes"], "recommended_ram_bytes")
    artifact_bytes = _positive_bytes(value["artifact_bytes"], "artifact_bytes")
    minimum_disk = _positive_bytes(value["minimum_free_disk_bytes"], "minimum_free_disk_bytes", zero=True)
    if recommended_ram < minimum_ram:
        raise LocalModelProfileError("recommended_ram_below_minimum")
    if minimum_disk < artifact_bytes:
        raise LocalModelProfileError("minimum_disk_below_artifact_size")

    systems, architectures = value["supported_operating_systems"], value["supported_architectures"]
    if (not isinstance(systems, list) or not systems or len(set(systems)) != len(systems)
            or any(item not in _SYSTEMS for item in systems)):
        raise LocalModelProfileError("invalid_supported_operating_systems")
    if (not isinstance(architectures, list) or not architectures
            or len(set(architectures)) != len(architectures)
            or any(item not in _ARCHITECTURES for item in architectures)):
        raise LocalModelProfileError("invalid_supported_architectures")
    requires_gpu = value["requires_gpu"]
    if type(requires_gpu) is not bool:
        raise LocalModelProfileError("invalid_requires_gpu")
    vram = value["minimum_vram_bytes"]
    if requires_gpu:
        vram = _positive_bytes(vram, "minimum_vram_bytes")
    elif vram is not None:
        raise LocalModelProfileError("vram_must_be_null_for_cpu_model")
    context = value["context_tokens"]
    if type(context) is not int or not 128 <= context <= 2_000_000:
        raise LocalModelProfileError("invalid_context_tokens")
    runtime = value["runtime"]
    if not isinstance(runtime, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", runtime):
        raise LocalModelProfileError("invalid_runtime")

    return {
        "schema_version": 1,
        "model_id": model_id,
        "model_version": version,
        "artifact_sha256": digest,
        "source_url": source_url,
        "license_id": license_id,
        "format": value["format"],
        "quantization": value["quantization"],
        "minimum_ram_bytes": minimum_ram,
        "recommended_ram_bytes": recommended_ram,
        "artifact_bytes": artifact_bytes,
        "minimum_free_disk_bytes": minimum_disk,
        "supported_operating_systems": list(systems),
        "supported_architectures": list(architectures),
        "requires_gpu": requires_gpu,
        "minimum_vram_bytes": vram,
        "context_tokens": context,
        "runtime": runtime,
    }


def profile_digest(profile: Mapping[str, Any]) -> str:
    normalized = validate_local_model_profile(profile)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def admit_catalog_profile(
    profile: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    os_name: str,
    architecture: str,
    runtime_budget: int | None,
    free_disk: int,
    verified_gpu_memory_bytes: int | None = None,
) -> dict[str, Any]:
    """Score a profile only after matching it to an approved catalog record."""
    normalized = validate_local_model_profile(profile)
    models = catalog.get("models") if isinstance(catalog, Mapping) else None
    if catalog.get("schema_version") != 1 or not isinstance(models, list):
        raise LocalModelProfileError("invalid_model_catalog")
    matches = [
        item for item in models
        if isinstance(item, Mapping)
        and item.get("model_id") == normalized["model_id"]
        and item.get("model_version") == normalized["model_version"]
    ]
    if len(matches) != 1:
        raise LocalModelProfileError("catalog_model_not_found_or_ambiguous")
    item = matches[0]
    if item.get("state") not in {"approved", "staged", "active"}:
        raise LocalModelProfileError("catalog_model_not_approved")
    if item.get("artifact_sha256") != normalized["artifact_sha256"]:
        raise LocalModelProfileError("catalog_artifact_digest_mismatch")
    if item.get("license_id") != normalized["license_id"]:
        raise LocalModelProfileError("catalog_license_mismatch")
    if item.get("provenance_status") != "verified" or item.get("license_status") != "verified":
        raise LocalModelProfileError("catalog_provenance_or_license_unverified")
    if item.get("evaluation_passed") is not True:
        raise LocalModelProfileError("catalog_evaluation_not_passed")

    readiness = _model_compatibility(
        {
            "model_id": normalized["model_id"],
            "minimum_ram_bytes": normalized["minimum_ram_bytes"],
            "recommended_ram_bytes": normalized["recommended_ram_bytes"],
            "download_bytes": normalized["artifact_bytes"],
            "minimum_free_disk_bytes": normalized["minimum_free_disk_bytes"],
            "supported_operating_systems": normalized["supported_operating_systems"],
            "supported_architectures": normalized["supported_architectures"],
            "requires_gpu": normalized["requires_gpu"],
        },
        os_name=os_name,
        architecture=architecture,
        runtime_budget=runtime_budget,
        free_disk=free_disk,
    )
    if normalized["requires_gpu"]:
        if (type(verified_gpu_memory_bytes) is not int
                or verified_gpu_memory_bytes < normalized["minimum_vram_bytes"]):
            readiness["compatible"] = False
            readiness["recommended"] = False
            readiness["status"] = "not_admitted"
            readiness["reasons"].append("insufficient_or_unverified_gpu_memory")
    return {
        "profile": normalized,
        "profile_sha256": profile_digest(normalized),
        "catalog_state": item["state"],
        "readiness": readiness,
        "admission": "eligible_for_runtime_preflight" if readiness["compatible"] else "rejected",
        "limitations": [
            "Catalog approval and resource metadata do not replace runtime load-time validation.",
            "The caller must verify the artifact bytes against artifact_sha256 before loading.",
            "This function does not download, install, activate or execute a model.",
        ],
    }
