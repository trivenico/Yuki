"""Deterministic, offline evaluation runner for Yuki's golden scenarios."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    input_text: str
    expected_contains: tuple[str, ...] = ()
    expected_not_contains: tuple[str, ...] = ()
    expected_ok: bool | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    failures: tuple[str, ...]
    output: str


def run_case(case: EvalCase, evaluator: Callable[[str], object]) -> EvalResult:
    try:
        raw = evaluator(case.input_text)
        if isinstance(raw, str):
            output, ok = raw, None
        elif isinstance(raw, dict):
            output = str(raw.get("message", raw.get("output", "")))
            ok = raw.get("ok")
        else:
            output = str(getattr(raw, "message", raw))
            ok = getattr(raw, "ok", None)
    except Exception as exc:
        return EvalResult(case.case_id, False, (f"evaluator raised {type(exc).__name__}",), "")
    failures = []
    folded = output.casefold()
    for expected in case.expected_contains:
        if expected.casefold() not in folded:
            failures.append(f"missing required phrase: {expected!r}")
    for forbidden in case.expected_not_contains:
        if forbidden.casefold() in folded:
            failures.append(f"contains forbidden phrase: {forbidden!r}")
    if case.expected_ok is not None and ok is not None and bool(ok) != case.expected_ok:
        failures.append(f"expected ok={case.expected_ok}, got {ok}")
    return EvalResult(case.case_id, not failures, tuple(failures), output[:2000])


def run_dataset(cases: Iterable[EvalCase], evaluator: Callable[[str], object]) -> dict:
    results = [run_case(case, evaluator) for case in cases]
    return {
        "total": len(results),
        "passed": sum(result.passed for result in results),
        "failed": sum(not result.passed for result in results),
        "results": [asdict(result) for result in results],
    }


def write_report(report: dict, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
