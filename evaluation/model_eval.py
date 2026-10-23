"""Run bounded checks against an explicitly supplied model adapter.

This evaluates observable responses, not model weights or code safety. It never
installs models, downloads artifacts, or grants tools to the model.
"""
from __future__ import annotations
import json, time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

@dataclass(frozen=True)
class EvalCase:
    case_id: str
    task: str
    prompt: str
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    max_latency_seconds: float = 30.0
    max_response_chars: int = 20_000

@dataclass(frozen=True)
class CaseResult:
    case_id: str
    task: str
    passed: bool
    latency_seconds: float
    response_chars: int
    missing_required_terms: tuple[str, ...]
    forbidden_terms_found: tuple[str, ...]
    error_category: str | None

@dataclass(frozen=True)
class EvalReport:
    candidate_id: str
    generated_at: str
    case_count: int
    passed_count: int
    pass_rate: float
    total_duration_seconds: float
    results: tuple[CaseResult, ...]
    promotion_eligible: bool
    promotion_reasons: tuple[str, ...]
    def to_dict(self) -> dict[str, Any]: return asdict(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

class ModelEvaluator:
    """Evaluate an injected callable using deterministic task-specific assertions."""
    def __init__(self, *, max_cases: int = 50, max_prompt_chars: int = 8_000,
                 max_total_duration_seconds: float = 180.0, minimum_pass_rate: float = 0.9):
        if not 1 <= max_cases <= 200: raise ValueError("max_cases must be between 1 and 200")
        if not 1 <= max_prompt_chars <= 50_000: raise ValueError("max_prompt_chars must be between 1 and 50000")
        if not 1 <= max_total_duration_seconds <= 1800: raise ValueError("max_total_duration_seconds must be between 1 and 1800")
        if not 0.0 <= minimum_pass_rate <= 1.0: raise ValueError("minimum_pass_rate must be between 0 and 1")
        self.max_cases, self.max_prompt_chars = max_cases, max_prompt_chars
        self.max_total_duration_seconds, self.minimum_pass_rate = max_total_duration_seconds, minimum_pass_rate

    @staticmethod
    def _validate_case(case: EvalCase) -> None:
        if not isinstance(case, EvalCase): raise TypeError("cases must be EvalCase instances")
        if not case.case_id or len(case.case_id) > 100 or not case.case_id.replace("_", "").replace("-", "").isalnum(): raise ValueError("case_id must be a short alphanumeric identifier")
        if not case.task or len(case.task) > 64: raise ValueError("task must be a non-empty short string")
        if not isinstance(case.prompt, str): raise TypeError("prompt must be a string")
        if not 0 < case.max_latency_seconds <= 1800: raise ValueError("max_latency_seconds must be in (0, 1800]")
        if not 1 <= case.max_response_chars <= 200_000: raise ValueError("max_response_chars must be between 1 and 200000")

    def run(self, candidate_id: str, generate: Callable[[str], str], cases: Iterable[EvalCase], *, approval_required: bool = True) -> EvalReport:
        if not isinstance(candidate_id, str) or not candidate_id.strip() or len(candidate_id) > 200: raise ValueError("candidate_id must be a non-empty identifier up to 200 characters")
        if not callable(generate): raise TypeError("generate must be a callable accepting a prompt")
        case_list = list(cases)
        if not case_list or len(case_list) > self.max_cases: raise ValueError(f"case count must be between 1 and {self.max_cases}")
        for case in case_list:
            self._validate_case(case)
            if len(case.prompt) > self.max_prompt_chars: raise ValueError(f"prompt for case {case.case_id!r} exceeds configured limit")
        if len({case.case_id for case in case_list}) != len(case_list): raise ValueError("case_id values must be unique")
        started, results = time.monotonic(), []
        for case in case_list:
            if time.monotonic() - started >= self.max_total_duration_seconds:
                results.append(CaseResult(case.case_id, case.task, False, 0.0, 0, (), (), "suite_deadline")); continue
            began, response, error_category = time.monotonic(), "", None
            try:
                # The adapter must enforce a real timeout; synchronous Python calls cannot be preempted here.
                output = generate(case.prompt)
                if not isinstance(output, str): raise TypeError("provider returned non-string response")
                response = output[:case.max_response_chars + 1]
                if len(output) > case.max_response_chars: error_category = "response_too_large"
            except TimeoutError: error_category = "timeout"
            except Exception as exc: error_category = "provider_error:" + type(exc).__name__[:60]
            latency = time.monotonic() - began
            if latency > case.max_latency_seconds and error_category is None: error_category = "latency_limit_exceeded"
            lowered = response.casefold()
            missing = tuple(term for term in case.required_terms if term.casefold() not in lowered)
            forbidden = tuple(term for term in case.forbidden_terms if term.casefold() in lowered)
            results.append(CaseResult(case.case_id, case.task, error_category is None and not missing and not forbidden,
                round(latency, 4), min(len(response), case.max_response_chars), missing, forbidden, error_category))
        duration = time.monotonic() - started
        passed = sum(result.passed for result in results); rate = passed / len(results)
        reasons = []
        if rate < self.minimum_pass_rate: reasons.append("pass_rate_below_threshold")
        if any(result.error_category for result in results): reasons.append("one_or_more_cases_had_runtime_errors_or_limits")
        if duration > self.max_total_duration_seconds: reasons.append("suite_duration_exceeded")
        if approval_required: reasons.append("human_approval_required")
        return EvalReport(candidate_id, datetime.now(timezone.utc).isoformat(), len(results), passed, round(rate, 4), round(duration, 4), tuple(results), not reasons, tuple(reasons))

    def qualify(self, report: EvalReport, *, license_approved: bool, security_review_passed: bool,
                compatibility_passed: bool, human_approved: bool) -> dict[str, Any]:
        gates = {"evaluation_passed": report.pass_rate >= self.minimum_pass_rate and not any(r.error_category for r in report.results),
                 "license_approved": bool(license_approved), "security_review_passed": bool(security_review_passed),
                 "compatibility_passed": bool(compatibility_passed), "human_approved": bool(human_approved)}
        return {"candidate_id": report.candidate_id, "eligible_for_staged_trial": all(gates.values()), "gates": gates,
                "automatic_production_promotion": False,
                "note": "Eligibility permits only a separately configured staged trial; it never changes production routing."}
