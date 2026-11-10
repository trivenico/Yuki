"""Local, evidence-based model registry and human-reviewed recommendations.

Only sanitized metrics and caller-supplied benchmark identifiers are stored. No
model is activated automatically. This is a local file tool, not a multi-user DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Iterable

import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_BENCHMARK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_MAX_REGISTRY_BYTES = 5_000_000
_MAX_RECORDS = 5000


class RegistryError(RuntimeError):
    """Registry input or persistence error."""


def _finite_nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return float(value)


def validate_evaluation(report, *, benchmark_id, license_status="unknown", runtime_status="unknown", cost_per_1k_tokens=None):
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("unsupported evaluation report schema")
    model_id = report.get("model_id")
    if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
        raise ValueError("invalid model identifier")
    if not isinstance(benchmark_id, str) or not _BENCHMARK_ID.fullmatch(benchmark_id):
        raise ValueError("benchmark_id must be a short stable identifier")
    if report.get("source") != "huggingface_inference_router":
        raise ValueError("unsupported evaluation source")
    successful = report.get("successful_cases")
    failed = report.get("failed_cases")
    count = report.get("case_count")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (successful, failed, count)):
        raise ValueError("invalid evaluation counts")
    if successful + failed != count or count < 1 or count > 20:
        raise ValueError("inconsistent or out-of-range evaluation counts")
    score = report.get("mean_expected_term_coverage")
    if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1):
        raise ValueError("coverage score must be between 0 and 1")
    latency = report.get("mean_latency_ms")
    if latency is not None:
        latency = _finite_nonnegative(latency, "mean_latency_ms")
    if license_status not in {"verified", "restricted", "unknown", "not_applicable"}:
        raise ValueError("invalid license_status")
    if runtime_status not in {"verified", "incompatible", "unknown"}:
        raise ValueError("invalid runtime_status")
    cost = None if cost_per_1k_tokens is None else _finite_nonnegative(cost_per_1k_tokens, "cost_per_1k_tokens")
    return {
        "model_id": model_id,
        "benchmark_id": benchmark_id,
        "source": report["source"],
        "successful_cases": successful,
        "failed_cases": failed,
        "case_count": count,
        "coverage": float(score) if score is not None else None,
        "mean_latency_ms": latency,
        "license_status": license_status,
        "runtime_status": runtime_status,
        "cost_per_1k_tokens": cost,
        "review_status": "needs_human_review",
        "automatic_activation": False,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def load_registry(path):
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "records": []}
    try:
        if path.stat().st_size > _MAX_REGISTRY_BYTES:
            raise RegistryError("registry file exceeds size limit")
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError("could not read valid registry JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("records"), list):
        raise RegistryError("unsupported registry schema")
    if len(data["records"]) > _MAX_RECORDS:
        raise RegistryError("registry record limit exceeded")
    return data


def save_registry(path, data):
    path = Path(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("records"), list):
        raise RegistryError("invalid registry structure")
    if len(data["records"]) > _MAX_RECORDS:
        raise RegistryError("registry record limit exceeded")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".yuki-registry-", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        raise RegistryError("could not save registry") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def record_evaluation(registry, record):
    if not isinstance(registry, dict) or registry.get("schema_version") != 1 or not isinstance(registry.get("records"), list):
        raise RegistryError("invalid registry structure")
    key = (record["model_id"], record["benchmark_id"])
    records = registry["records"]
    for index, previous in enumerate(records):
        if (previous.get("model_id"), previous.get("benchmark_id")) == key:
            records[index] = record
            return registry
    if len(records) >= _MAX_RECORDS:
        raise RegistryError("registry record limit exceeded")
    records.append(record)
    return registry


def recommend(registry, *, benchmark_id, max_results=10):
    if not isinstance(benchmark_id, str) or not _BENCHMARK_ID.fullmatch(benchmark_id):
        raise ValueError("invalid benchmark_id")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")
    records = registry.get("records") if isinstance(registry, dict) else None
    if not isinstance(records, list):
        raise RegistryError("invalid registry structure")
    eligible = []
    excluded = []
    for item in records:
        if not isinstance(item, dict) or item.get("benchmark_id") != benchmark_id:
            continue
        reason = None
        if item.get("license_status") == "restricted":
            reason = "license_restricted"
        elif item.get("runtime_status") == "incompatible":
            reason = "runtime_incompatible"
        elif item.get("license_status") != "verified":
            reason = "license_not_verified"
        elif item.get("runtime_status") != "verified":
            reason = "runtime_not_verified"
        elif not isinstance(item.get("coverage"), (int, float)) or not math.isfinite(item["coverage"]):
            reason = "no_comparable_coverage_score"
        elif item.get("successful_cases", 0) < 1:
            reason = "no_successful_cases"
        if reason:
            excluded.append({"model_id": item.get("model_id"), "reason": reason})
        else:
            eligible.append(item)
    eligible.sort(key=lambda item: (
        -float(item["coverage"]),
        float(item["mean_latency_ms"]) if isinstance(item.get("mean_latency_ms"), (int, float)) else float("inf"),
        item["model_id"],
    ))
    return {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "recommendations": [{
            "rank": rank,
            "model_id": item["model_id"],
            "coverage": item["coverage"],
            "mean_latency_ms": item.get("mean_latency_ms"),
            "successful_cases": item["successful_cases"],
            "failed_cases": item["failed_cases"],
            "cost_per_1k_tokens": item.get("cost_per_1k_tokens"),
            "reason": "highest expected-term coverage among license- and runtime-verified candidates; latency breaks ties",
            "review_status": "needs_human_review",
            "automatic_activation": False,
        } for rank, item in enumerate(eligible[:max_results], start=1)],
        "excluded_candidates": excluded,
        "selection_policy": "same_benchmark_then_coverage_then_latency",
        "activation_policy": "manual_review_required",
        "limitations": [
            "Expected-term coverage is a narrow proxy, not general quality or safety.",
            "Cost is displayed only when supplied and is not independently verified.",
            "License and runtime status must be set only after separate review.",
            "Ranking is not a deployment approval and never changes the active model.",
        ],
    }


# Model capability metadata is intentionally separate from evaluation reports:
# a benchmark score does not prove that a model supports a modality or tool API.

MODEL_CAPABILITIES = frozenset({
    "text", "reasoning", "coding", "vision_input", "audio_input",
    "audio_output", "video_input", "tool_calling", "structured_output",
    "long_context",
})
_MODEL_LOCAL = frozenset({"local", "local_runtime", "local_only"})
_MODEL_CLOUD = frozenset({"cloud_provider", "third_party_gateway"})
_MODEL_PRIVACY = _MODEL_LOCAL | _MODEL_CLOUD
_MODEL_EVIDENCE = frozenset({
    "provider_documentation", "owner_verified", "local_probe", "benchmark_verified", "adapter_contract",
})


@dataclass(frozen=True)
class ModelCapabilityProfile:
    """Verified capability metadata for one exact provider/model pair."""
    provider: str
    model_id: str
    capabilities: frozenset[str]
    implemented: bool
    ready: bool
    privacy: str
    context_window: int | None = None
    evidence_source: str = "owner_verified"

    def __post_init__(self):
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be non-empty")
        if self.provider != self.provider.strip().lower():
            raise ValueError("provider must be normalized to lowercase")
        if not isinstance(self.model_id, str) or not _MODEL_ID.fullmatch(self.model_id):
            raise ValueError("model_id must use provider/model identifier syntax")
        if not isinstance(self.capabilities, frozenset):
            raise TypeError("capabilities must be a frozenset")
        if any(not isinstance(value, str) for value in self.capabilities):
            raise ValueError("capabilities must contain strings")
        unknown = self.capabilities - MODEL_CAPABILITIES
        if unknown:
            raise ValueError("unknown model capabilities: " + ", ".join(sorted(unknown)))
        if type(self.implemented) is not bool or type(self.ready) is not bool:
            raise TypeError("implemented and ready must be booleans")
        if self.ready and not self.implemented:
            raise ValueError("an unimplemented model cannot be ready")
        if self.privacy not in _MODEL_PRIVACY:
            raise ValueError("unknown model privacy classification")
        if self.context_window is not None and (
            isinstance(self.context_window, bool)
            or not isinstance(self.context_window, int)
            or not 1 <= self.context_window <= 10_000_000
        ):
            raise ValueError("context_window must be a positive bounded integer")
        if self.evidence_source not in _MODEL_EVIDENCE:
            raise ValueError("evidence_source must identify verified metadata")


class ModelCapabilityRegistry:
    """Deterministic exact-model selector; it never probes networks or activates models."""
    def __init__(self, profiles: Iterable[ModelCapabilityProfile] = ()):
        self._profiles: dict[tuple[str, str], ModelCapabilityProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: ModelCapabilityProfile) -> None:
        if not isinstance(profile, ModelCapabilityProfile):
            raise TypeError("profile must be a ModelCapabilityProfile")
        key = (profile.provider, profile.model_id)
        if key in self._profiles:
            raise ValueError("model profile already registered")
        self._profiles[key] = profile

    def get(self, provider: str, model_id: str):
        if not isinstance(provider, str) or not isinstance(model_id, str):
            return None
        return self._profiles.get((provider.strip().lower(), model_id))

    def list(self):
        return [{
            "provider": p.provider, "model_id": p.model_id,
            "capabilities": sorted(p.capabilities), "implemented": p.implemented,
            "ready": p.ready, "privacy": p.privacy,
            "context_window": p.context_window, "evidence_source": p.evidence_source,
        } for p in sorted(self._profiles.values(), key=lambda item: (item.provider, item.model_id))]

    def route(self, *, required=("text",), provider_preference=(), model_preference=(),
              allow_cloud=False, strict_provider_preference=False, min_context_window=None):
        if any(isinstance(value, (str, bytes)) for value in
               (required, provider_preference, model_preference)):
            raise TypeError("capabilities and preferences must be iterables, not strings")
        if type(allow_cloud) is not bool or type(strict_provider_preference) is not bool:
            raise TypeError("routing flags must be booleans")
        if min_context_window is not None and (
            isinstance(min_context_window, bool) or not isinstance(min_context_window, int)
            or min_context_window < 1
        ):
            raise ValueError("min_context_window must be a positive integer")
        try:
            required_values = tuple(islice(iter(required), 11))
            providers = tuple(islice(iter(provider_preference), 65))
            models = tuple(islice(iter(model_preference), 65))
        except TypeError as exc:
            raise TypeError("routing inputs must be iterable") from exc
        if len(required_values) > 10:
            raise ValueError("too many required capabilities")
        if len(providers) > 64 or len(models) > 64:
            raise ValueError("too many preferences")
        if any(not isinstance(value, str) for value in required_values):
            raise ValueError("required capabilities must be strings")
        required_set = frozenset(required_values)
        unknown = required_set - MODEL_CAPABILITIES
        if unknown:
            raise ValueError("unknown required capabilities: " + ", ".join(sorted(unknown)))

        def normalize(values, lowercase):
            result, seen = [], set()
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("preferences must contain non-empty strings")
                item = value.strip().lower() if lowercase else value.strip()
                if item not in seen:
                    result.append(item)
                    seen.add(item)
            return result

        providers = normalize(providers, True)
        models = normalize(models, False)
        provider_rank = {value: index for index, value in enumerate(providers)}
        model_rank = {value: index for index, value in enumerate(models)}
        candidates = [
            p for p in self._profiles.values()
            if p.implemented and p.ready and required_set.issubset(p.capabilities)
            and (p.privacy in _MODEL_LOCAL or (allow_cloud and p.privacy in _MODEL_CLOUD))
            and (not strict_provider_preference or p.provider in provider_rank)
            and (min_context_window is None or (
                p.context_window is not None and p.context_window >= min_context_window
            ))
        ]
        candidates.sort(key=lambda p: (
            model_rank.get(p.model_id, len(model_rank)),
            provider_rank.get(p.provider, len(provider_rank)),
            0 if p.privacy in _MODEL_LOCAL else 1, p.provider, p.model_id,
        ))
        if not candidates:
            raise LookupError("no model satisfies capabilities, readiness and privacy policy")
        return candidates[0]
