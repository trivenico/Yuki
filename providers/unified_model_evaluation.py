"""Provider-neutral, privacy-conscious model candidate evaluation.

The caller supplies an inference function. This module does not select providers,
download weights, execute model code, or activate candidates.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Callable

from providers.evaluation_stats import compute_stats
from providers.model_evaluation import score_response, validate_cases

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class CandidateEvaluationError(ValueError):
    """Invalid candidate metadata or benchmark configuration."""


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CandidateEvaluationError(f"invalid_{label}")
    return value


def evaluate_candidate(
    *,
    model_id: str,
    provider_id: str,
    runtime_id: str,
    benchmark_id: str,
    benchmark_version: str,
    cases: list[dict],
    generate: Callable[[str], str],
    artifact_sha256: str | None = None,
    max_cases: int = 20,
) -> dict:
    """Evaluate one candidate against a bounded benchmark using a supplied adapter.

    Reports contain case IDs and aggregate/term metrics only. Prompts and generated
    responses are intentionally excluded. Expected-term coverage is a narrow proxy,
    not a general measure of model quality.
    """
    model_id = _identity(model_id, "model_id")
    provider_id = _identity(provider_id, "provider_id")
    runtime_id = _identity(runtime_id, "runtime_id")
    benchmark_id = _identity(benchmark_id, "benchmark_id")
    benchmark_version = _identity(benchmark_version, "benchmark_version")
    if not callable(generate):
        raise CandidateEvaluationError("generate_must_be_callable")
    if artifact_sha256 is not None and (
        not isinstance(artifact_sha256, str) or not _SHA256.fullmatch(artifact_sha256)
    ):
        raise CandidateEvaluationError("artifact_sha256_must_be_64_hex_characters")

    validated = validate_cases(cases, max_cases=max_cases)
    canonical = json.dumps(validated, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    dataset_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    results = []
    for case in validated:
        started = time.monotonic()
        try:
            response = generate(case["prompt"])
            if not isinstance(response, str):
                raise TypeError("model_response_must_be_text")
            status, error = "ok", None
        except Exception:
            response, status, error = "", "error", "inference_failed"
        latency = round((time.monotonic() - started) * 1000, 2)
        metrics = score_response(response, case["expected_terms"])
        results.append({
            "case_id": case["id"],
            "status": status,
            "error": error,
            "latency_ms": latency,
            "score": metrics["score"],
            "metric": metrics["metric"],
            "matched_terms": metrics["matched_terms"],
            "missing_terms": metrics["missing_terms"],
        })

    successful = [item for item in results if item["status"] == "ok"]
    scores = [item["score"] for item in successful if item["score"] is not None]
    latencies = [item["latency_ms"] for item in successful]
    report = {
        "schema_version": 1,
        "model_id": model_id,
        "provider_id": provider_id,
        "runtime_id": runtime_id,
        "artifact_sha256": artifact_sha256.lower() if artifact_sha256 else None,
        "benchmark_id": benchmark_id,
        "benchmark_version": benchmark_version,
        "dataset_sha256": dataset_digest,
        "case_count": len(results),
        "successful_cases": len(successful),
        "failed_cases": len(results) - len(successful),
        "mean_expected_term_coverage": round(sum(scores) / len(scores), 4) if scores else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "results": results,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "activation_policy": "manual_review_required",
        "limitations": [
            "Expected-term coverage is a narrow task-specific proxy, not general quality, factuality or safety.",
            "Candidate rankings require the same benchmark ID, version and dataset digest.",
            "Artifact identity is only as trustworthy as the caller-supplied digest and provenance verification.",
            "Evaluation never activates or changes the active model.",
        ],
    }
    report.update(compute_stats("latency_ms", latencies))
    return report


def comparable_reports(left: dict, right: dict) -> bool:
    """True only when two well-formed reports describe the exact same benchmark data."""
    keys = ("benchmark_id", "benchmark_version", "dataset_sha256")
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and all(isinstance(left.get(key), str) and left.get(key) == right.get(key) for key in keys)
        and bool(_SHA256.fullmatch(left["dataset_sha256"]))
    )
