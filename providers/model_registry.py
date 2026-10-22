"""Local, evidence-based model registry and human-reviewed recommendations.

Only sanitized metrics and caller-supplied benchmark identifiers are stored. No
model is activated automatically. This is a local file tool, not a multi-user DB.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
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
