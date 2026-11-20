"""Deterministic regression-test selection hints; never executes tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from automation.workspace_change_impact import WorkspaceChangeImpact


@dataclass(frozen=True)
class RegressionTestCandidate:
    path: str
    priority: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RegressionTestPlan:
    candidates: tuple[RegressionTestCandidate, ...]
    full_suite_recommended: bool
    full_suite_reasons: tuple[str, ...]
    requires_human_review: bool = True
    tests_executed: bool = False


def prepare_regression_test_plan(
    impact: WorkspaceChangeImpact,
    available_test_paths: tuple[str, ...],
) -> RegressionTestPlan:
    """Rank known test files using impact evidence and conservative risk rules."""
    if not isinstance(impact, WorkspaceChangeImpact):
        raise TypeError("impact must be a WorkspaceChangeImpact.")
    if not isinstance(available_test_paths, tuple):
        raise TypeError("available_test_paths must be an immutable tuple.")
    if any(not isinstance(path, str) or not path.strip() for path in available_test_paths):
        raise ValueError("test paths must be non-empty text.")
    if len(set(available_test_paths)) != len(available_test_paths):
        raise ValueError("available test paths must be unique.")

    available = set(available_test_paths)
    reasons_by_path: dict[str, set[str]] = {}
    for path in impact.test_candidates:
        if path in available:
            reasons_by_path.setdefault(path, set()).add("identified by impact analysis")
    for path in impact.direct_importers:
        if path in available:
            reasons_by_path.setdefault(path, set()).add("direct importer of a changed module")
    for path in impact.transitive_importers:
        if path in available:
            reasons_by_path.setdefault(path, set()).add("transitive importer of a changed module")

    candidates = []
    for path, reasons in reasons_by_path.items():
        priority = "high" if (
            "direct importer of a changed module" in reasons
        ) else "normal"
        candidates.append(RegressionTestCandidate(
            path=path,
            priority=priority,
            reasons=tuple(sorted(reasons)),
        ))
    candidates.sort(key=lambda item: (0 if item.priority == "high" else 1, item.path))

    full_reasons = []
    if impact.configuration_paths:
        full_reasons.append("configuration changes can affect behavior beyond local imports")
    if impact.dependency_paths:
        full_reasons.append("dependency changes can affect the wider environment")
    if impact.security_sensitive_paths:
        full_reasons.append("security-sensitive paths warrant broad regression coverage")
    if impact.parse_errors:
        full_reasons.append("one or more source files could not be analyzed")
    if not candidates:
        full_reasons.append("no existing targeted test files were identified")
    return RegressionTestPlan(
        candidates=tuple(candidates),
        full_suite_recommended=bool(full_reasons),
        full_suite_reasons=tuple(full_reasons),
    )
