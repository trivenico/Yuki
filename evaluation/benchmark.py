"""Deterministic, privacy-minimizing benchmark evaluation for recorded model outputs.

This module scores supplied outputs only. It never calls a model, executes tools,
trains weights, or promotes a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Iterable

MAX_CASES = 10_000
MAX_TEXT_CHARS = 32_768
MAX_MODEL_ID_CHARS = 128
MAX_CATEGORY_CHARS = 64
MAX_COST_USD = 1_000_000.0
MAX_LATENCY_MS = 86_400_000.0


class EvaluationError(ValueError):
    """Raised when benchmark input is invalid or exceeds configured bounds."""


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    category: str
    expected_output_sha256: str
    actual_output_sha256: str
    exact_match: bool
    latency_ms: float
    cost_usd: float
    safety_violation: bool


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    schema_version: int
    benchmark_id: str
    model_id: str
    model_version: str
    evaluated_at: str
    cases: tuple[BenchmarkCase, ...]
    total_cases: int
    exact_matches: int
    accuracy: float
    mean_latency_ms: float
    p95_latency_ms: float
    total_cost_usd: float
    safety_violations: int
    safety_violation_rate: float
    report_sha256: str
    provenance: str = "owner_supplied_unverified"
    verified: bool = False


def _identifier(value: str, field: str, limit: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EvaluationError(f"invalid {field}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise EvaluationError(f"invalid {field}")
    return value


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise EvaluationError("invalid evaluated_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationError("invalid evaluated_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationError("evaluated_at must include a timezone")
    return value


def _output_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
        raise EvaluationError(f"invalid {field}")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _metric(value: object, field: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= maximum:
        raise EvaluationError(f"{field} is outside the supported range")
    return result


def _canonical_digest(payload: dict) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def evaluate_benchmark(
    observations: Iterable[dict],
    *,
    benchmark_id: str,
    model_id: str,
    model_version: str,
    evaluated_at: str,
) -> BenchmarkReport:
    """Score bounded exact-match observations and aggregate reproducible metrics.

    Each observation has exactly: case_id, category, expected_output,
    actual_output, latency_ms, cost_usd, safety_violation. Text is hashed and
    discarded; the report does not retain prompts or model output contents.
    """
    benchmark_id = _identifier(benchmark_id, "benchmark_id")
    model_id = _identifier(model_id, "model_id")
    model_version = _identifier(model_version, "model_version")
    timestamp = _timestamp(evaluated_at)
    if isinstance(observations, (str, bytes, dict)):
        raise EvaluationError("observations must be an iterable of case objects")

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    try:
        iterator = iter(observations)
    except TypeError as exc:
        raise EvaluationError("observations must be iterable") from exc

    for item in iterator:
        if len(cases) >= MAX_CASES:
            raise EvaluationError("benchmark exceeds case limit")
        if not isinstance(item, dict) or set(item) != {
            "case_id", "category", "expected_output", "actual_output",
            "latency_ms", "cost_usd", "safety_violation",
        }:
            raise EvaluationError("each case must contain the exact supported fields")
        case_id = _identifier(item["case_id"], "case_id")
        if case_id in seen:
            raise EvaluationError("duplicate case_id")
        seen.add(case_id)
        category = item["category"]
        if not isinstance(category, str) or not category.strip() or len(category) > MAX_CATEGORY_CHARS:
            raise EvaluationError("invalid category")
        if not isinstance(item["safety_violation"], bool):
            raise EvaluationError("safety_violation must be boolean")
        expected = _output_digest(item["expected_output"], "expected_output")
        actual = _output_digest(item["actual_output"], "actual_output")
        cases.append(BenchmarkCase(
            case_id=case_id,
            category=category,
            expected_output_sha256=expected,
            actual_output_sha256=actual,
            exact_match=expected == actual,
            latency_ms=_metric(item["latency_ms"], "latency_ms", MAX_LATENCY_MS),
            cost_usd=_metric(item["cost_usd"], "cost_usd", MAX_COST_USD),
            safety_violation=item["safety_violation"],
        ))
    if not cases:
        raise EvaluationError("benchmark must contain at least one case")

    ordered_latency = sorted(case.latency_ms for case in cases)
    p95_index = max(0, math.ceil(0.95 * len(ordered_latency)) - 1)
    matched = sum(case.exact_match for case in cases)
    violations = sum(case.safety_violation for case in cases)
    total = len(cases)
    report_payload = {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "model_id": model_id,
        "model_version": model_version,
        "evaluated_at": timestamp,
        "cases": [{
            "case_id": c.case_id, "category": c.category,
            "expected_output_sha256": c.expected_output_sha256,
            "actual_output_sha256": c.actual_output_sha256,
            "exact_match": c.exact_match, "latency_ms": c.latency_ms,
            "cost_usd": c.cost_usd, "safety_violation": c.safety_violation,
        } for c in cases],
        "total_cases": total,
        "exact_matches": matched,
        "accuracy": matched / total,
        "mean_latency_ms": sum(ordered_latency) / total,
        "p95_latency_ms": ordered_latency[p95_index],
        "total_cost_usd": sum(c.cost_usd for c in cases),
        "safety_violations": violations,
        "safety_violation_rate": violations / total,
        "provenance": "owner_supplied_unverified",
        "verified": False,
    }
    return BenchmarkReport(
        schema_version=1, benchmark_id=benchmark_id, model_id=model_id,
        model_version=model_version, evaluated_at=timestamp,
        cases=tuple(cases), total_cases=total, exact_matches=matched,
        accuracy=matched / total,
        mean_latency_ms=report_payload["mean_latency_ms"],
        p95_latency_ms=ordered_latency[p95_index],
        total_cost_usd=report_payload["total_cost_usd"],
        safety_violations=violations,
        safety_violation_rate=violations / total,
        report_sha256=_canonical_digest(report_payload),
    )


def validate_benchmark_report(report: BenchmarkReport) -> bool:
    """Verify report integrity and recompute all aggregate values."""
    if not isinstance(report, BenchmarkReport):
        return False
    try:
        observations = [{
            "case_id": c.case_id, "category": c.category,
            "expected_output": c.expected_output_sha256,
            "actual_output": c.actual_output_sha256,
            "latency_ms": c.latency_ms, "cost_usd": c.cost_usd,
            "safety_violation": c.safety_violation,
        } for c in report.cases]
        # Rebuild the canonical payload directly: the stored hashes are evidence,
        # not plaintext outputs, so recomputation does not pretend to re-run scoring.
        if report.schema_version != 1 or report.provenance != "owner_supplied_unverified" or report.verified:
            return False
        if not report.cases or len(report.cases) > MAX_CASES or report.total_cases != len(report.cases):
            return False
        if len({c.case_id for c in report.cases}) != len(report.cases):
            return False
        if any(not re.fullmatch(r"[a-f0-9]{64}", c.expected_output_sha256) or
               not re.fullmatch(r"[a-f0-9]{64}", c.actual_output_sha256) or
               c.exact_match != (c.expected_output_sha256 == c.actual_output_sha256)
               for c in report.cases):
            return False
        latency = sorted(_metric(c.latency_ms, "latency_ms", MAX_LATENCY_MS) for c in report.cases)
        costs = [_metric(c.cost_usd, "cost_usd", MAX_COST_USD) for c in report.cases]
        matches = sum(c.exact_match for c in report.cases)
        violations = sum(c.safety_violation is True for c in report.cases)
        if any(not isinstance(c.safety_violation, bool) for c in report.cases):
            return False
        p95 = latency[max(0, math.ceil(0.95 * len(latency)) - 1)]
        if (report.exact_matches != matches or report.accuracy != matches / len(report.cases)
            or report.mean_latency_ms != sum(latency) / len(latency)
            or report.p95_latency_ms != p95 or report.total_cost_usd != sum(costs)
            or report.safety_violations != violations
            or report.safety_violation_rate != violations / len(report.cases)):
            return False
        _identifier(report.benchmark_id, "benchmark_id")
        _identifier(report.model_id, "model_id")
        _identifier(report.model_version, "model_version")
        timestamp = _timestamp(report.evaluated_at)
        payload = {
            "schema_version": 1, "benchmark_id": report.benchmark_id,
            "model_id": report.model_id, "model_version": report.model_version,
            "evaluated_at": timestamp,
            "cases": [{
                "case_id": c.case_id, "category": c.category,
                "expected_output_sha256": c.expected_output_sha256,
                "actual_output_sha256": c.actual_output_sha256,
                "exact_match": c.exact_match, "latency_ms": c.latency_ms,
                "cost_usd": c.cost_usd, "safety_violation": c.safety_violation,
            } for c in report.cases],
            "total_cases": len(report.cases), "exact_matches": matches,
            "accuracy": matches / len(report.cases),
            "mean_latency_ms": sum(latency) / len(latency),
            "p95_latency_ms": p95, "total_cost_usd": sum(costs),
            "safety_violations": violations,
            "safety_violation_rate": violations / len(report.cases),
            "provenance": "owner_supplied_unverified", "verified": False,
        }
        return report.report_sha256 == _canonical_digest(payload)
    except (EvaluationError, AttributeError, TypeError, ValueError):
        return False
