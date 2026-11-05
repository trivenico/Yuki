"""Quarantined dataset provenance manifests for Yuki training inputs.

A manifest records source and review evidence; it does not prove that a license,
consent claim, or source revision is valid. Training eligibility is fail-closed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,199}$")
_REVIEW_STATES = {"unverified", "verified", "rejected"}
_APPROVAL_STATES = {"quarantined", "approved", "rejected"}


class DatasetManifestError(ValueError):
    """Invalid dataset manifest or unsafe training-input state."""


_FIELDS = {
    "schema_version", "dataset_id", "dataset_revision", "source_uri",
    "source_revision", "artifact_sha256", "license_id", "license_evidence",
    "rights_status", "consent_status", "privacy_status", "review_status",
    "size_bytes", "file_count", "retrieved_at", "contains_executable_code",
}


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DatasetManifestError("invalid_" + field)
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z"):
        raise DatasetManifestError("invalid_retrieved_at")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("timestamp_must_be_utc")
    except ValueError as exc:
        raise DatasetManifestError("invalid_retrieved_at") from exc
    return value


def validate_dataset_manifest(value: Any) -> dict:
    """Validate and normalize a strict schema-v1 dataset manifest."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise DatasetManifestError("unsupported_manifest_schema")
    if set(value) != _FIELDS:
        raise DatasetManifestError("unexpected_manifest_fields")

    dataset_id = _identifier(value["dataset_id"], "dataset_id")
    dataset_revision = _identifier(value["dataset_revision"], "dataset_revision")
    source_uri = value["source_uri"]
    if not isinstance(source_uri, str) or not source_uri.strip() or len(source_uri) > 2048:
        raise DatasetManifestError("invalid_source_uri")
    source_revision = _identifier(value["source_revision"], "source_revision")
    artifact_sha = value["artifact_sha256"]
    if not isinstance(artifact_sha, str) or not _SHA256.fullmatch(artifact_sha):
        raise DatasetManifestError("invalid_artifact_sha256")
    license_id = _identifier(value["license_id"], "license_id")
    license_evidence = value["license_evidence"]
    if not isinstance(license_evidence, str) or not license_evidence.strip() or len(license_evidence) > 2048:
        raise DatasetManifestError("invalid_license_evidence")

    statuses = {}
    for field in ("rights_status", "consent_status", "privacy_status", "review_status"):
        status = value[field]
        allowed = _APPROVAL_STATES if field == "review_status" else _REVIEW_STATES
        if not isinstance(status, str) or status not in allowed:
            raise DatasetManifestError("invalid_" + field)
        statuses[field] = status

    size_bytes, file_count = value["size_bytes"], value["file_count"]
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 1:
        raise DatasetManifestError("invalid_size_bytes")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 1:
        raise DatasetManifestError("invalid_file_count")
    executable = value["contains_executable_code"]
    if not isinstance(executable, bool):
        raise DatasetManifestError("invalid_contains_executable_code")

    normalized = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "dataset_revision": dataset_revision,
        "source_uri": source_uri.strip(),
        "source_revision": source_revision,
        "artifact_sha256": artifact_sha.lower(),
        "license_id": license_id,
        "license_evidence": license_evidence.strip(),
        **statuses,
        "size_bytes": size_bytes,
        "file_count": file_count,
        "retrieved_at": _timestamp(value["retrieved_at"]),
        "contains_executable_code": executable,
    }
    normalized["training_eligible"] = (
        statuses["rights_status"] == "verified"
        and statuses["consent_status"] == "verified"
        and statuses["privacy_status"] == "verified"
        and statuses["review_status"] == "approved"
        and not executable
    )
    normalized["quarantine_required"] = not normalized["training_eligible"]
    return normalized


def assert_training_eligible(manifest: Any) -> dict:
    """Return a verified manifest or fail closed before training admission."""
    normalized = validate_dataset_manifest(manifest)
    if not normalized["training_eligible"]:
        raise DatasetManifestError("dataset_not_approved_for_training")
    return normalized
