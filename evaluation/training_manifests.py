"""Fail-closed validation for pinned training and model-candidate manifests.

Validation is structural and policy-oriented only: it never downloads datasets or
weights, executes repository code, verifies remote review identities, or promotes
a candidate. Approval claims remain untrusted until checked by an authorized
control-plane workflow.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit


class ManifestValidationError(ValueError):
    """Raised when a training manifest is malformed or violates policy."""


_SHA1 = re.compile(r"^[a-f0-9]{40}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DATASET_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,80}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SPLIT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MANIFEST_PATH = re.compile(r"^[A-Za-z0-9_./-]+\.json$")


def _object(value: Any, field: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{field} must be an object")
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        raise ManifestValidationError(f"{field} missing required fields: {', '.join(sorted(missing))}")
    if extra:
        raise ManifestValidationError(f"{field} contains unsupported fields: {', '.join(sorted(extra))}")
    return value


def _string(value: Any, field: str, *, minimum: int = 1, maximum: int = 200) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or value != value.strip():
        raise ManifestValidationError(f"{field} must be a trimmed string of length {minimum}..{maximum}")
    return value


def _digest(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    result = _string(value, field, maximum=pattern.pattern.count("a") + 64)
    if not pattern.fullmatch(result):
        raise ManifestValidationError(f"{field} must be a valid lowercase digest")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    raw = _string(value, field, maximum=64)
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as exc:
        raise ManifestValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManifestValidationError(f"{field} must include a timezone")
    return parsed


def _uri(value: Any, field: str, schemes: tuple[str, ...] = ("https",)) -> str:
    raw = _string(value, field, maximum=2048)
    parsed = urlsplit(raw)
    if parsed.scheme not in schemes or not parsed.netloc or parsed.username or parsed.password:
        raise ManifestValidationError(f"{field} must be an absolute URI using {', '.join(schemes)} without embedded credentials")
    return raw


def _status(value: Any, field: str) -> str:
    if value not in ("approved", "rejected", "pending"):
        raise ManifestValidationError(f"{field} must be approved, rejected, or pending")
    return value


def validate_training_dataset_manifest(data: Any, *, require_approved: bool = True) -> dict:
    """Validate a dataset manifest; personal-data datasets fail closed by default."""
    manifest = _object(data, "manifest", {
        "schema_version", "dataset_id", "hub_repository", "revision", "license",
        "source_url", "retrieved_at", "splits", "intended_use", "content_sha256",
        "privacy_review",
    }, {"notes"})
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ManifestValidationError("unsupported schema_version")
    dataset_id = _string(manifest["dataset_id"], "dataset_id", maximum=81)
    if not _DATASET_ID.fullmatch(dataset_id):
        raise ManifestValidationError("invalid dataset_id")
    repository = _string(manifest["hub_repository"], "hub_repository", maximum=193)
    if not _REPOSITORY.fullmatch(repository):
        raise ManifestValidationError("hub_repository must be an owner/name identifier")
    _digest(manifest["revision"], "revision", _SHA1)
    source = _uri(manifest["source_url"], "source_url")
    if not source.startswith("https://huggingface.co/datasets/"):
        raise ManifestValidationError("source_url must identify a Hugging Face dataset")
    _timestamp(manifest["retrieved_at"], "retrieved_at")
    _digest(manifest["content_sha256"], "content_sha256", _SHA256)
    _string(manifest["intended_use"], "intended_use", minimum=10, maximum=1000)
    if "notes" in manifest:
        _string(manifest["notes"], "notes", maximum=2000)
    splits = manifest["splits"]
    if not isinstance(splits, list) or not splits or len(splits) > 128:
        raise ManifestValidationError("splits must be a non-empty bounded list")
    if any(not isinstance(item, str) or not _SPLIT.fullmatch(item) for item in splits) or len(set(splits)) != len(splits):
        raise ManifestValidationError("splits contain invalid or duplicate names")

    license_info = _object(manifest["license"], "license", {
        "spdx_id", "review_status", "reviewed_by", "reviewed_at",
    }, {"evidence_url"})
    _string(license_info["spdx_id"], "license.spdx_id", maximum=100)
    license_status = _status(license_info["review_status"], "license.review_status")
    _string(license_info["reviewed_by"], "license.reviewed_by", maximum=100)
    _timestamp(license_info["reviewed_at"], "license.reviewed_at")
    if "evidence_url" in license_info:
        _uri(license_info["evidence_url"], "license.evidence_url")

    privacy = _object(manifest["privacy_review"], "privacy_review", {
        "status", "contains_personal_data", "reviewed_at",
    }, {"notes"})
    privacy_status = _status(privacy["status"], "privacy_review.status")
    if type(privacy["contains_personal_data"]) is not bool:
        raise ManifestValidationError("privacy_review.contains_personal_data must be boolean")
    _timestamp(privacy["reviewed_at"], "privacy_review.reviewed_at")
    if "notes" in privacy:
        _string(privacy["notes"], "privacy_review.notes", maximum=2000)
    if privacy["contains_personal_data"]:
        raise ManifestValidationError("datasets containing personal data require a separately approved privacy workflow")
    if require_approved and (license_status != "approved" or privacy_status != "approved"):
        raise ManifestValidationError("dataset license and privacy reviews must both be approved")
    return manifest


def validate_model_candidate_manifest(data: Any, *, require_qualified: bool = False) -> dict:
    """Validate a candidate record; optional qualification requires all declared gates.

    This function cannot authenticate who set review fields or verify remote
    artifacts. Callers must independently verify signatures, digests, provenance,
    evaluation reports, and reviewer authority before any promotion decision.
    """
    manifest = _object(data, "manifest", {
        "schema_version", "candidate_id", "base_model", "base_revision",
        "training_code_commit", "dataset_manifests", "artifact", "training",
        "evaluation", "reviews", "rollback_candidate",
    })
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ManifestValidationError("unsupported schema_version")
    candidate_id = _string(manifest["candidate_id"], "candidate_id", maximum=101)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,100}", candidate_id):
        raise ManifestValidationError("invalid candidate_id")
    _string(manifest["base_model"], "base_model", minimum=3, maximum=200)
    _digest(manifest["base_revision"], "base_revision", _SHA1)
    _digest(manifest["training_code_commit"], "training_code_commit", _SHA1)

    paths = manifest["dataset_manifests"]
    if not isinstance(paths, list) or not paths or len(paths) > 128:
        raise ManifestValidationError("dataset_manifests must be a non-empty bounded list")
    for path in paths:
        if not isinstance(path, str) or not _MANIFEST_PATH.fullmatch(path) or ".." in path.split("/"):
            raise ManifestValidationError("dataset_manifests contains an unsafe manifest path")

    artifact = _object(manifest["artifact"], "artifact", {"uri", "sha256", "format"}, {"size_bytes"})
    _uri(artifact["uri"], "artifact.uri", ("https", "s3", "oci"))
    _digest(artifact["sha256"], "artifact.sha256", _SHA256)
    if artifact["format"] not in ("safetensors", "onnx", "gguf", "other"):
        raise ManifestValidationError("unsupported artifact.format")
    if "size_bytes" in artifact and (type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 1):
        raise ManifestValidationError("artifact.size_bytes must be a positive integer")

    training = _object(manifest["training"], "training", {
        "platform", "runtime_image", "python_version", "seed", "config_sha256",
        "started_at", "completed_at",
    }, {"accelerator"})
    if training["platform"] not in ("kaggle", "other"):
        raise ManifestValidationError("unsupported training.platform")
    _string(training["runtime_image"], "training.runtime_image")
    python_version = _string(training["python_version"], "training.python_version", maximum=32)
    if not re.fullmatch(r"3\.[0-9]+\.[0-9]+", python_version):
        raise ManifestValidationError("training.python_version must be a full Python version")
    if type(training["seed"]) is not int or not 0 <= training["seed"] <= 2147483647:
        raise ManifestValidationError("training.seed must be a bounded non-negative integer")
    _digest(training["config_sha256"], "training.config_sha256", _SHA256)
    started = _timestamp(training["started_at"], "training.started_at")
    completed = _timestamp(training["completed_at"], "training.completed_at")
    if completed < started:
        raise ManifestValidationError("training.completed_at cannot precede started_at")
    if "accelerator" in training:
        _string(training["accelerator"], "training.accelerator", maximum=100)

    evaluation = _object(manifest["evaluation"], "evaluation", {
        "report_uri", "report_sha256", "suite_revision", "status",
    })
    _uri(evaluation["report_uri"], "evaluation.report_uri", ("https", "s3", "oci"))
    _digest(evaluation["report_sha256"], "evaluation.report_sha256", _SHA256)
    _digest(evaluation["suite_revision"], "evaluation.suite_revision", _SHA1)
    if evaluation["status"] not in ("passed", "failed", "pending"):
        raise ManifestValidationError("invalid evaluation.status")

    reviews = _object(manifest["reviews"], "reviews", {
        "license", "security", "human_approval",
    }, {"reviewed_by", "reviewed_at"})
    statuses = [_status(reviews[key], f"reviews.{key}") for key in ("license", "security", "human_approval")]
    if "reviewed_by" in reviews:
        _string(reviews["reviewed_by"], "reviews.reviewed_by", maximum=100)
    if "reviewed_at" in reviews:
        _timestamp(reviews["reviewed_at"], "reviews.reviewed_at")
    rollback = manifest["rollback_candidate"]
    if rollback is not None:
        _string(rollback, "rollback_candidate", maximum=100)
        if rollback == candidate_id:
            raise ManifestValidationError("candidate cannot be its own rollback target")

    if require_qualified and (evaluation["status"] != "passed" or any(s != "approved" for s in statuses)):
        raise ManifestValidationError("candidate has not passed evaluation and all required review gates")
    return manifest
