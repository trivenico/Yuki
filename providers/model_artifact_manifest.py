"""Validated provenance manifests for Yuki-owned and imported model artifacts.

A manifest records claims and hashes; it does not establish that a license is
compatible or that provenance claims are truthful. Those require independent review.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_LICENSE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+()-]{0,79}$")
_MAX_DATASETS = 128


class ModelArtifactManifestError(ValueError):
    """Invalid model artifact manifest or artifact input."""


def sha256_file(path: str | Path, *, max_bytes: int = 20 * 1024**4) -> str:
    """Hash a regular, non-symlink file without loading it into memory."""
    target = Path(path)
    if target.is_symlink():
        raise ModelArtifactManifestError("artifact_must_not_be_symlink")
    try:
        if not target.is_file():
            raise ModelArtifactManifestError("artifact_must_be_regular_file")
        size = target.stat().st_size
        if size < 1 or size > max_bytes:
            raise ModelArtifactManifestError("artifact_size_out_of_bounds")
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ModelArtifactManifestError("artifact_unreadable") from exc


def validate_manifest(value: Any) -> dict:
    """Validate and return a normalized schema-v1 manifest."""
    allowed = {"schema_version", "model_id", "model_version", "artifact_sha256", "source",
               "license_id", "base_model", "training_method", "dataset_sha256", "created_at"}
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ModelArtifactManifestError("unsupported_manifest_schema")
    if set(value) != allowed:
        raise ModelArtifactManifestError("unexpected_manifest_fields")

    def identity(field: str, *, optional: bool = False):
        item = value.get(field)
        if optional and item is None:
            return None
        if not isinstance(item, str) or not _ID.fullmatch(item):
            raise ModelArtifactManifestError("invalid_" + field)
        return item

    model_id = identity("model_id")
    model_version = identity("model_version")
    artifact_sha = value.get("artifact_sha256")
    if not isinstance(artifact_sha, str) or not _SHA256.fullmatch(artifact_sha):
        raise ModelArtifactManifestError("invalid_artifact_sha256")

    license_id = value.get("license_id")
    if not isinstance(license_id, str) or not _LICENSE.fullmatch(license_id):
        raise ModelArtifactManifestError("invalid_license_id")

    source = value.get("source")
    if not isinstance(source, str) or not _ID.fullmatch(source):
        raise ModelArtifactManifestError("invalid_source")

    base_model = identity("base_model", optional=True)
    training_method = value.get("training_method")
    if not isinstance(training_method, str) or training_method not in {"from_scratch", "full_finetune", "lora", "qlora", "imported"}:
        raise ModelArtifactManifestError("invalid_training_method")

    datasets = value.get("dataset_sha256")
    if not isinstance(datasets, list) or len(datasets) > _MAX_DATASETS:
        raise ModelArtifactManifestError("invalid_dataset_sha256")
    if any(not isinstance(d, str) or not _SHA256.fullmatch(d) for d in datasets):
        raise ModelArtifactManifestError("invalid_dataset_sha256")
    if len(set(d.lower() for d in datasets)) != len(datasets):
        raise ModelArtifactManifestError("duplicate_dataset_digest")

    created_at = value.get("created_at")
    if not isinstance(created_at, str) or len(created_at) > 40 or not created_at.endswith("Z"):
        raise ModelArtifactManifestError("invalid_created_at")
    try:
        from datetime import datetime, timezone
        parsed_created_at = datetime.fromisoformat(created_at[:-1] + "+00:00")
        if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() != timezone.utc.utcoffset(parsed_created_at):
            raise ValueError("timestamp_must_be_utc")
    except ValueError as exc:
        raise ModelArtifactManifestError("invalid_created_at") from exc

    return {
        "schema_version": 1,
        "model_id": model_id,
        "model_version": model_version,
        "artifact_sha256": artifact_sha.lower(),
        "source": source,
        "license_id": license_id,
        "base_model": base_model,
        "training_method": training_method,
        "dataset_sha256": sorted(d.lower() for d in datasets),
        "created_at": created_at,
        "provenance_review": "unverified",
        "activation_policy": "manual_review_required",
    }


def verify_artifact(path: str | Path, manifest: dict) -> bool:
    """Compare file bytes to the declared digest; does not verify provenance/license."""
    normalized = validate_manifest(manifest)
    return sha256_file(path) == normalized["artifact_sha256"]


def canonical_manifest_json(manifest: dict) -> str:
    """Stable serialization suitable for hashing or version control."""
    return json.dumps(validate_manifest(manifest), sort_keys=True, separators=(",", ":"))
