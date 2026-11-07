"""Immutable, fail-closed experiment specifications for Yuki model training.

This module records reproducible intent and admission evidence. It does not
download weights, allocate GPUs, launch training, or publish/activate checkpoints.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone

from providers.dataset_artifact_manifest import (
    DatasetManifestError,
    assert_training_eligible,
)

_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}$")
_METHODS = {"lora", "qlora", "full_finetune"}
_HYPERPARAMETERS = {
    "epochs", "learning_rate", "batch_size", "gradient_accumulation_steps",
    "max_sequence_length", "warmup_ratio", "weight_decay", "lora_rank",
    "lora_alpha", "lora_dropout", "quantization_bits",
}
_LIMITS = {
    "epochs": (1, 100),
    "batch_size": (1, 1024),
    "gradient_accumulation_steps": (1, 1024),
    "max_sequence_length": (128, 131072),
    "lora_rank": (1, 512),
    "lora_alpha": (1, 2048),
    "quantization_bits": (4, 8),
}


class TrainingExperimentError(ValueError):
    """Experiment configuration or training admission failed."""


def _id(value, field):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise TrainingExperimentError("invalid_" + field)
    return value


def _digest(value, field):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise TrainingExperimentError("invalid_" + field)
    return value.lower()


def _hyperparameters(value, method):
    if not isinstance(value, dict) or not value or set(value) - _HYPERPARAMETERS:
        raise TrainingExperimentError("invalid_hyperparameters")
    normalized = {}
    for key, item in value.items():
        if key in _LIMITS:
            low, high = _LIMITS[key]
            if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
                raise TrainingExperimentError("invalid_hyperparameter_" + key)
        elif key in {"learning_rate", "warmup_ratio", "weight_decay", "lora_dropout"}:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise TrainingExperimentError("invalid_hyperparameter_" + key)
            if key == "learning_rate" and not 0 < item <= 1:
                raise TrainingExperimentError("invalid_hyperparameter_" + key)
            if key in {"warmup_ratio", "lora_dropout"} and not 0 <= item < 1:
                raise TrainingExperimentError("invalid_hyperparameter_" + key)
            if key == "weight_decay" and not 0 <= item <= 10:
                raise TrainingExperimentError("invalid_hyperparameter_" + key)
            normalized[key] = float(item)
            continue
        normalized[key] = item
    if method in {"lora", "qlora"} and not {"lora_rank", "lora_alpha"}.issubset(normalized):
        raise TrainingExperimentError("lora_configuration_required")
    if method == "qlora" and normalized.get("quantization_bits") != 4:
        raise TrainingExperimentError("qlora_requires_four_bit_quantization")
    if method != "qlora" and "quantization_bits" in normalized:
        raise TrainingExperimentError("quantization_only_supported_for_qlora")
    return normalized


def create_experiment_spec(
    *,
    experiment_id,
    model_id,
    model_version,
    training_method,
    base_model_sha256,
    base_model_evidence,
    datasets,
    split_manifest,
    benchmark_id,
    benchmark_version,
    baseline_evaluation_sha256,
    seed,
    hyperparameters,
    created_at=None,
):
    """Create a content-addressed training plan only from approved dataset evidence."""
    experiment_id = _id(experiment_id, "experiment_id")
    model_id = _id(model_id, "model_id")
    model_version = _id(model_version, "model_version")
    benchmark_id = _id(benchmark_id, "benchmark_id")
    benchmark_version = _id(benchmark_version, "benchmark_version")
    if training_method not in _METHODS:
        raise TrainingExperimentError("invalid_training_method")
    base_digest = _digest(base_model_sha256, "base_model_sha256")
    if not isinstance(base_model_evidence, dict) or set(base_model_evidence) != {
        "model_id", "source_uri", "source_revision", "artifact_sha256",
        "license_id", "license_evidence", "rights_status",
        "fine_tuning_allowed", "commercial_use_allowed",
    }:
        raise TrainingExperimentError("invalid_base_model_evidence")
    for field in ("model_id", "license_id"):
        _id(base_model_evidence.get(field), "base_model_" + field)
    for field in ("source_uri", "source_revision", "license_evidence"):
        value = base_model_evidence.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            raise TrainingExperimentError("invalid_base_model_" + field)
    if _digest(base_model_evidence.get("artifact_sha256"), "base_model_artifact_sha256") != base_digest:
        raise TrainingExperimentError("base_model_digest_mismatch")
    if (base_model_evidence.get("rights_status") != "verified"
            or base_model_evidence.get("fine_tuning_allowed") is not True
            or base_model_evidence.get("commercial_use_allowed") is not True):
        raise TrainingExperimentError("base_model_rights_not_approved")
    baseline_digest = _digest(baseline_evaluation_sha256, "baseline_evaluation_sha256")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**63 - 1:
        raise TrainingExperimentError("invalid_seed")
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= 128:
        raise TrainingExperimentError("invalid_datasets")
    if not isinstance(split_manifest, dict) or split_manifest.get("schema_version") != 1:
        raise TrainingExperimentError("invalid_split_manifest")
    split_digest = _digest(split_manifest.get("split_manifest_sha256"), "split_manifest_sha256")
    split_payload = {key: value for key, value in split_manifest.items()
                     if key not in {"split_manifest_sha256", "schema_version"}}
    calculated_split = hashlib.sha256(
        json.dumps(split_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if calculated_split != split_digest:
        raise TrainingExperimentError("split_manifest_digest_mismatch")
    splits = split_manifest.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "validation", "test"}:
        raise TrainingExperimentError("invalid_split_membership")
    split_sets = []
    for name in ("train", "validation", "test"):
        items = splits[name]
        if not isinstance(items, list) or not items or any(
            not isinstance(item, str) or not _SHA256.fullmatch(item) for item in items
        ):
            raise TrainingExperimentError("invalid_split_membership")
        if len(set(items)) != len(items):
            raise TrainingExperimentError("duplicate_split_member")
        split_sets.append(set(items))
    if any(split_sets[i] & split_sets[j] for i in range(3) for j in range(i + 1, 3)):
        raise TrainingExperimentError("split_leakage_detected")

    dataset_evidence = []
    seen = set()
    for manifest in datasets:
        try:
            approved = assert_training_eligible(manifest)
        except DatasetManifestError as exc:
            raise TrainingExperimentError("dataset_not_approved_for_training") from exc
        key = (approved["dataset_id"], approved["dataset_revision"])
        if key in seen:
            raise TrainingExperimentError("duplicate_dataset_revision")
        seen.add(key)
        dataset_evidence.append({
            "dataset_id": approved["dataset_id"],
            "dataset_revision": approved["dataset_revision"],
            "artifact_sha256": approved["artifact_sha256"],
        })
    dataset_evidence.sort(key=lambda item: (item["dataset_id"], item["dataset_revision"]))
    split_members = set().union(*split_sets)
    if not split_members:
        raise TrainingExperimentError("empty_split_manifest")

    params = _hyperparameters(hyperparameters, training_method)
    timestamp = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not isinstance(timestamp, str) or len(timestamp) > 40 or not timestamp.endswith("Z"):
        raise TrainingExperimentError("invalid_created_at")
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
        if parsed.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise TrainingExperimentError("invalid_created_at") from exc

    payload = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "model_id": model_id,
        "model_version": model_version,
        "training_method": training_method,
        "base_model_sha256": base_digest,
        "base_model_evidence": {
            **base_model_evidence,
            "artifact_sha256": base_digest,
        },
        "datasets": dataset_evidence,
        "split_manifest_sha256": split_digest,
        "split_counts": {name: len(splits[name]) for name in ("train", "validation", "test")},
        "benchmark_id": benchmark_id,
        "benchmark_version": benchmark_version,
        "baseline_evaluation_sha256": baseline_digest,
        "seed": seed,
        "hyperparameters": params,
        "created_at": timestamp,
        "execution_policy": "manual_training_approval_required",
        "activation_policy": "manual_model_release_approval_required",
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    return {**payload, "experiment_sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def verify_experiment_spec(spec):
    """Verify a stored spec digest and key policy invariants without executing it."""
    if not isinstance(spec, dict):
        raise TrainingExperimentError("invalid_experiment_spec")
    digest = spec.get("experiment_sha256")
    payload = {key: value for key, value in spec.items() if key != "experiment_sha256"}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    actual = hashlib.sha256(canonical.encode()).hexdigest()
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or actual != digest:
        raise TrainingExperimentError("experiment_digest_mismatch")
    if spec.get("execution_policy") != "manual_training_approval_required":
        raise TrainingExperimentError("invalid_execution_policy")
    if spec.get("activation_policy") != "manual_model_release_approval_required":
        raise TrainingExperimentError("invalid_activation_policy")
    return True

def build_training_preflight(
    spec,
    *,
    worker,
    backend,
    estimated_peak_memory_bytes,
    required_storage_bytes,
    estimated_cost,
    budget_limit,
):
    """Create a fail-closed, non-executing resource-fit report for an experiment.

    Memory and cost values are caller/backend estimates, not measurements or
    guarantees. This function never dispatches a job or authorizes spending.
    """
    verify_experiment_spec(spec)
    if not isinstance(worker, dict) or set(worker) != {
        "worker_id", "online", "paired", "enabled", "backends",
        "ram_available_bytes", "storage_available_bytes",
    }:
        raise TrainingExperimentError("invalid_preflight_worker")
    _id(worker["worker_id"], "worker_id")
    for field in ("online", "paired", "enabled"):
        if type(worker[field]) is not bool:
            raise TrainingExperimentError("invalid_preflight_worker_" + field)
    if not isinstance(worker["backends"], (list, tuple)) or not worker["backends"]:
        raise TrainingExperimentError("invalid_preflight_worker_backends")
    if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", item)
           for item in worker["backends"]):
        raise TrainingExperimentError("invalid_preflight_worker_backends")
    if not isinstance(backend, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", backend):
        raise TrainingExperimentError("invalid_preflight_backend")

    for field, value in (
        ("estimated_peak_memory_bytes", estimated_peak_memory_bytes),
        ("required_storage_bytes", required_storage_bytes),
        ("available_memory_bytes", worker["ram_available_bytes"]),
        ("available_storage_bytes", worker["storage_available_bytes"]),
    ):
        if type(value) is not int or not 0 <= value <= 2**60:
            raise TrainingExperimentError("invalid_preflight_" + field)
    if not isinstance(estimated_cost, (int, float)) or isinstance(estimated_cost, bool) or not math.isfinite(estimated_cost) or estimated_cost < 0:
        raise TrainingExperimentError("invalid_preflight_estimated_cost")
    if not isinstance(budget_limit, (int, float)) or isinstance(budget_limit, bool) or not math.isfinite(budget_limit) or budget_limit < 0:
        raise TrainingExperimentError("invalid_preflight_budget_limit")

    blockers = []
    if not worker["online"]:
        blockers.append("worker_offline")
    if not worker["paired"] or not worker["enabled"]:
        blockers.append("worker_not_trusted_or_disabled")
    if backend not in worker["backends"]:
        blockers.append("backend_not_supported")
    if estimated_peak_memory_bytes > worker["ram_available_bytes"]:
        blockers.append("insufficient_memory")
    if required_storage_bytes > worker["storage_available_bytes"]:
        blockers.append("insufficient_storage")
    if estimated_cost > budget_limit:
        blockers.append("budget_limit_exceeded")
    return {
        "schema_version": 1,
        "experiment_id": spec["experiment_id"],
        "experiment_sha256": spec["experiment_sha256"],
        "worker_id": worker["worker_id"],
        "backend": backend,
        "estimated_peak_memory_bytes": estimated_peak_memory_bytes,
        "available_memory_bytes": worker["ram_available_bytes"],
        "required_storage_bytes": required_storage_bytes,
        "available_storage_bytes": worker["storage_available_bytes"],
        "estimated_cost": float(estimated_cost),
        "budget_limit": float(budget_limit),
        "estimates_are_advisory": True,
        "execution_dispatched": False,
        "approval_required": True,
        "status": "blocked" if blockers else "preflight_passed",
        "blockers": blockers,
    }

