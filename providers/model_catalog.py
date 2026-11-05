"""Conservative lifecycle catalog for verified model artifacts.

This catalog records review and release decisions. It does not load weights,
change a runtime, or activate a model in an inference provider.
"""
from __future__ import annotations

import re
from copy import deepcopy

from providers.model_artifact_manifest import (
    ModelArtifactManifestError,
    validate_manifest,
    verify_artifact,
)

_EVALUATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


class ModelCatalogError(ValueError):
    """Invalid model catalog operation."""


def new_catalog():
    return {"schema_version": 1, "models": []}


def _find(catalog, model_id, model_version):
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ModelCatalogError("unsupported_catalog_schema")
    models = catalog.get("models")
    if not isinstance(models, list):
        raise ModelCatalogError("invalid_catalog_models")
    for item in models:
        if isinstance(item, dict) and item.get("model_id") == model_id and item.get("model_version") == model_version:
            return item
    raise ModelCatalogError("model_not_found")


def _reviewer(value):
    if not isinstance(value, str) or not _REVIEWER_ID.fullmatch(value):
        raise ModelCatalogError("invalid_reviewer")
    return value


def register_artifact(catalog, manifest, artifact_path):
    """Verify bytes and register an artifact in quarantine; no provenance assumed."""
    try:
        normalized = validate_manifest(manifest)
        if not verify_artifact(artifact_path, normalized):
            raise ModelCatalogError("artifact_digest_mismatch")
    except ModelArtifactManifestError as exc:
        raise ModelCatalogError(str(exc)) from exc
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1 or not isinstance(catalog.get("models"), list):
        raise ModelCatalogError("unsupported_catalog_schema")
    if len(catalog["models"]) >= 5000:
        raise ModelCatalogError("catalog_limit_exceeded")
    key = (normalized["model_id"], normalized["model_version"])
    if any((x.get("model_id"), x.get("model_version")) == key for x in catalog["models"] if isinstance(x, dict)):
        raise ModelCatalogError("model_version_already_registered")
    method = normalized["training_method"]
    ownership_claim = "imported" if method == "imported" else (
        "yuki_trained_from_scratch" if method == "from_scratch" else "yuki_finetuned"
    )
    record = {
        "model_id": normalized["model_id"],
        "model_version": normalized["model_version"],
        "artifact_sha256": normalized["artifact_sha256"],
        "ownership_claim": ownership_claim,
        "ownership_status": "unverified",
        "training_method": method,
        "source": normalized["source"],
        "license_id": normalized["license_id"],
        "base_model": normalized["base_model"],
        "dataset_sha256": list(normalized["dataset_sha256"]),
        "state": "quarantined",
        "artifact_verified": True,
        "provenance_status": "unverified",
        "license_status": "unknown",
        "runtime_status": "unknown",
        "evaluation_id": None,
        "evaluation_passed": False,
        "approved_by": None,
        "activation_policy": "manual_review_required",
    }
    catalog["models"].append(record)
    return deepcopy(record)


def review_provenance(catalog, model_id, model_version, *, provenance_status, license_status, reviewer):
    item = _find(catalog, model_id, model_version)
    reviewer = _reviewer(reviewer)
    if item.get("state") != "quarantined":
        raise ModelCatalogError("provenance_review_requires_quarantine")
    if not isinstance(provenance_status, str) or provenance_status not in {"verified", "rejected"}:
        raise ModelCatalogError("invalid_provenance_status")
    if not isinstance(license_status, str) or license_status not in {"verified", "restricted", "not_applicable"}:
        raise ModelCatalogError("invalid_license_status")
    item["provenance_status"] = provenance_status
    item["license_status"] = license_status
    item["provenance_reviewer"] = reviewer
    if provenance_status == "rejected":
        item["state"] = "rejected"
    elif provenance_status == "verified" and license_status in {"verified", "not_applicable"}:
        item["state"] = "reviewed"
    return deepcopy(item)


def record_evaluation(catalog, model_id, model_version, *, evaluation_id, passed):
    item = _find(catalog, model_id, model_version)
    if item.get("state") != "reviewed":
        raise ModelCatalogError("evaluation_requires_reviewed_model")
    if not isinstance(evaluation_id, str) or not _EVALUATION_ID.fullmatch(evaluation_id):
        raise ModelCatalogError("invalid_evaluation_id")
    if not isinstance(passed, bool):
        raise ModelCatalogError("evaluation_result_must_be_boolean")
    item["evaluation_id"] = evaluation_id
    item["evaluation_passed"] = passed
    item["state"] = "evaluated"
    return deepcopy(item)


def approve_model(catalog, model_id, model_version, *, approver):
    item = _find(catalog, model_id, model_version)
    approver = _reviewer(approver)
    if item.get("state") != "evaluated" or item.get("evaluation_passed") is not True:
        raise ModelCatalogError("approval_requires_passing_evaluation")
    item["approved_by"] = approver
    item["state"] = "approved"
    return deepcopy(item)


def stage_model(catalog, model_id, model_version, *, runtime_status):
    item = _find(catalog, model_id, model_version)
    if item.get("state") != "approved":
        raise ModelCatalogError("staging_requires_approval")
    if runtime_status != "verified":
        raise ModelCatalogError("runtime_compatibility_not_verified")
    item["runtime_status"] = "verified"
    item["state"] = "staged"
    return deepcopy(item)


def mark_catalog_active(catalog, model_id, model_version, *, approver):
    """Mark a catalog release active; does not activate a runtime or load weights."""
    item = _find(catalog, model_id, model_version)
    approver = _reviewer(approver)
    if item.get("state") != "staged":
        raise ModelCatalogError("activation_requires_staged_model")
    for other in catalog["models"]:
        if other is not item and other.get("model_id") == model_id and other.get("state") == "active":
            other["state"] = "retired"
    item["release_approver"] = approver
    item["state"] = "active"
    return deepcopy(item)


def rollback_catalog_release(catalog, model_id, model_version, *, approver):
    item = _find(catalog, model_id, model_version)
    approver = _reviewer(approver)
    if item.get("state") != "active":
        raise ModelCatalogError("rollback_requires_active_release")
    item["rollback_by"] = approver
    item["state"] = "rolled_back"
    return deepcopy(item)


def list_models(catalog):
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1 or not isinstance(catalog.get("models"), list):
        raise ModelCatalogError("unsupported_catalog_schema")
    return deepcopy(catalog["models"])
