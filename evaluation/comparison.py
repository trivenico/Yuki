"""Deterministic comparison of two completed model evaluation reports.

This module compares recorded results only; it never invokes model adapters,
changes routing, or authorizes promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

from evaluation.model_eval import EvalReport

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_MAX_CASES = 200


class ComparisonError(ValueError):
    """Raised when evaluation reports cannot be safely compared."""


@dataclass(frozen=True, slots=True)
class CaseDelta:
    case_id: str
    baseline_passed: bool
    candidate_passed: bool
    status: str


@dataclass(frozen=True, slots=True)
class ModelComparison:
    schema_version: int
    benchmark_id: str
    benchmark_digest: str
    baseline_candidate_id: str
    candidate_id: str
    case_count: int
    baseline_pass_rate: float
    candidate_pass_rate: float
    pass_rate_delta: float
    regressions: int
    improvements: int
    unchanged_passes: int
    unchanged_failures: int
    cases: tuple[CaseDelta, ...]
    report_sha256: str
    promotion_recommendation: str = "human_review_required"
    provenance: str = "supplied_evaluation_reports_unverified"
    verified: bool = False


def _validate_report(report: EvalReport) -> None:
    if not isinstance(report, EvalReport):
        raise ComparisonError("both inputs must be EvalReport instances")
    if not isinstance(report.candidate_id, str) or not _ID.fullmatch(report.candidate_id):
        raise ComparisonError("invalid candidate ID")
    if type(report.case_count) is not int or not 1 <= report.case_count <= _MAX_CASES:
        raise ComparisonError("case count is outside supported bounds")
    if not isinstance(report.results, tuple) or len(report.results) != report.case_count:
        raise ComparisonError("report result count is inconsistent")
    ids = []
    for result in report.results:
        if not isinstance(result.case_id, str) or not result.case_id or len(result.case_id) > 100:
            raise ComparisonError("invalid case ID")
        if type(result.passed) is not bool:
            raise ComparisonError("case pass state must be boolean")
        if not isinstance(result.task, str) or not result.task or len(result.task) > 64:
            raise ComparisonError("invalid task label")
        if isinstance(result.latency_seconds, bool) or not isinstance(result.latency_seconds, (int, float)):
            raise ComparisonError("invalid case latency")
        if not math.isfinite(result.latency_seconds) or result.latency_seconds < 0:
            raise ComparisonError("invalid case latency")
        if type(result.response_chars) is not int or result.response_chars < 0:
            raise ComparisonError("invalid response size")
        if result.error_category is not None and (
            not isinstance(result.error_category, str) or len(result.error_category) > 100
        ):
            raise ComparisonError("invalid error category")
        ids.append(result.case_id)
    if len(set(ids)) != len(ids):
        raise ComparisonError("duplicate case IDs")
    passed = sum(item.passed for item in report.results)
    if type(report.passed_count) is not int or report.passed_count != passed:
        raise ComparisonError("passed count is inconsistent")
    if isinstance(report.pass_rate, bool) or not isinstance(report.pass_rate, (int, float)):
        raise ComparisonError("invalid pass rate")
    if not math.isfinite(report.pass_rate) or report.pass_rate != round(passed / report.case_count, 4):
        raise ComparisonError("pass rate is inconsistent")


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_evaluations(
    baseline: EvalReport,
    candidate: EvalReport,
    *,
    benchmark_id: str,
    benchmark_digest: str,
) -> ModelComparison:
    """Compare reports only when they cover exactly the same immutable cases."""
    _validate_report(baseline)
    _validate_report(candidate)
    if baseline.candidate_id == candidate.candidate_id:
        raise ComparisonError("baseline and candidate IDs must differ")
    if not isinstance(benchmark_id, str) or not _ID.fullmatch(benchmark_id):
        raise ComparisonError("invalid benchmark ID")
    if not isinstance(benchmark_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", benchmark_digest):
        raise ComparisonError("benchmark_digest must be a SHA-256 digest")
    baseline_results = {item.case_id: item for item in baseline.results}
    candidate_results = {item.case_id: item for item in candidate.results}
    if baseline_results.keys() != candidate_results.keys():
        raise ComparisonError("reports must cover the exact same benchmark case IDs")
    deltas = []
    for case_id in sorted(baseline_results):
        old, new = baseline_results[case_id], candidate_results[case_id]
        if old.task != new.task:
            raise ComparisonError("task labels differ for a shared case")
        if not old.passed and new.passed:
            status = "improved"
        elif old.passed and not new.passed:
            status = "regressed"
        elif old.passed:
            status = "unchanged_pass"
        else:
            status = "unchanged_failure"
        deltas.append(CaseDelta(case_id, old.passed, new.passed, status))
    regressions = sum(item.status == "regressed" for item in deltas)
    improvements = sum(item.status == "improved" for item in deltas)
    unchanged_passes = sum(item.status == "unchanged_pass" for item in deltas)
    unchanged_failures = sum(item.status == "unchanged_failure" for item in deltas)
    count = len(deltas)
    delta = round(candidate.pass_rate - baseline.pass_rate, 4)
    payload = {
        "schema_version": 1, "benchmark_id": benchmark_id,
        "benchmark_digest": benchmark_digest,
        "baseline_candidate_id": baseline.candidate_id,
        "candidate_id": candidate.candidate_id, "case_count": count,
        "baseline_pass_rate": baseline.pass_rate,
        "candidate_pass_rate": candidate.pass_rate, "pass_rate_delta": delta,
        "regressions": regressions, "improvements": improvements,
        "unchanged_passes": unchanged_passes,
        "unchanged_failures": unchanged_failures,
        "cases": [{"case_id": item.case_id,
                   "baseline_passed": item.baseline_passed,
                   "candidate_passed": item.candidate_passed,
                   "status": item.status} for item in deltas],
        "promotion_recommendation": "human_review_required",
        "provenance": "supplied_evaluation_reports_unverified",
        "verified": False,
    }
    return ModelComparison(
        schema_version=1, benchmark_id=benchmark_id,
        benchmark_digest=benchmark_digest,
        baseline_candidate_id=baseline.candidate_id,
        candidate_id=candidate.candidate_id, case_count=count,
        baseline_pass_rate=baseline.pass_rate,
        candidate_pass_rate=candidate.pass_rate, pass_rate_delta=delta,
        regressions=regressions, improvements=improvements,
        unchanged_passes=unchanged_passes, unchanged_failures=unchanged_failures,
        cases=tuple(deltas), report_sha256=_digest(payload),
    )


def validate_comparison(comparison: ModelComparison) -> bool:
    """Validate the comparison's schema, case states, aggregates and digest."""
    if not isinstance(comparison, ModelComparison):
        return False
    try:
        if (type(comparison.schema_version) is not int or comparison.schema_version != 1
            or type(comparison.verified) is not bool or comparison.verified
            or comparison.provenance != "supplied_evaluation_reports_unverified"
            or comparison.promotion_recommendation != "human_review_required"
            or type(comparison.case_count) is not int
            or not 1 <= comparison.case_count <= _MAX_CASES
            or not isinstance(comparison.cases, tuple)
            or len(comparison.cases) != comparison.case_count):
            return False
        for value in (comparison.baseline_pass_rate, comparison.candidate_pass_rate, comparison.pass_rate_delta):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return False
        if (not isinstance(comparison.benchmark_id, str) or not _ID.fullmatch(comparison.benchmark_id)
            or not isinstance(comparison.benchmark_digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", comparison.benchmark_digest)
            or not isinstance(comparison.baseline_candidate_id, str)
            or not _ID.fullmatch(comparison.baseline_candidate_id)
            or not isinstance(comparison.candidate_id, str)
            or not _ID.fullmatch(comparison.candidate_id)
            or comparison.baseline_candidate_id == comparison.candidate_id
            or not isinstance(comparison.report_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", comparison.report_sha256)):
            return False
        if any(not isinstance(item, CaseDelta) for item in comparison.cases):
            return False
        ids = [item.case_id for item in comparison.cases]
        if any(not isinstance(value, str) or not value or len(value) > 100 for value in ids):
            return False
        if len(set(ids)) != len(ids):
            return False
        expected = {
            (True, False): "regressed",
            (False, True): "improved",
            (True, True): "unchanged_pass",
            (False, False): "unchanged_failure",
        }
        counts = {name: 0 for name in expected.values()}
        for item in comparison.cases:
            if type(item.baseline_passed) is not bool or type(item.candidate_passed) is not bool:
                return False
            status = expected[(item.baseline_passed, item.candidate_passed)]
            if item.status != status:
                return False
            counts[status] += 1
        if (counts["regressed"] != comparison.regressions
            or counts["improved"] != comparison.improvements
            or counts["unchanged_pass"] != comparison.unchanged_passes
            or counts["unchanged_failure"] != comparison.unchanged_failures):
            return False
        if any(type(value) is not int or value < 0 for value in (comparison.regressions, comparison.improvements, comparison.unchanged_passes, comparison.unchanged_failures)):
            return False
        baseline_passes = counts["regressed"] + counts["unchanged_pass"]
        candidate_passes = counts["improved"] + counts["unchanged_pass"]
        if (comparison.baseline_pass_rate != round(baseline_passes / comparison.case_count, 4)
            or comparison.candidate_pass_rate != round(candidate_passes / comparison.case_count, 4)
            or comparison.pass_rate_delta != round(comparison.candidate_pass_rate - comparison.baseline_pass_rate, 4)):
            return False
        payload = {
            "schema_version": 1, "benchmark_id": comparison.benchmark_id,
            "benchmark_digest": comparison.benchmark_digest,
            "baseline_candidate_id": comparison.baseline_candidate_id,
            "candidate_id": comparison.candidate_id, "case_count": comparison.case_count,
            "baseline_pass_rate": comparison.baseline_pass_rate,
            "candidate_pass_rate": comparison.candidate_pass_rate,
            "pass_rate_delta": comparison.pass_rate_delta,
            "regressions": comparison.regressions, "improvements": comparison.improvements,
            "unchanged_passes": comparison.unchanged_passes,
            "unchanged_failures": comparison.unchanged_failures,
            "cases": [{"case_id": item.case_id, "baseline_passed": item.baseline_passed,
                       "candidate_passed": item.candidate_passed, "status": item.status}
                      for item in comparison.cases],
            "promotion_recommendation": "human_review_required",
            "provenance": "supplied_evaluation_reports_unverified", "verified": False,
        }
        return comparison.report_sha256 == _digest(payload)
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
        return False
