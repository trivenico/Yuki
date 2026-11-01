"""Trusted GitHub Actions evidence adapter for evolution verification.

The injected fetcher must call GitHub's authenticated API (or an equally
trusted installation client). Agent-supplied workflow JSON is not accepted
as a trusted source by this module's intended integration.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Callable, Mapping

from automation.evolution_review_verifier import (
    CheckResult,
    EvolutionReviewError,
    VerificationReport,
)

_SHA = re.compile(r"^[a-f0-9]{40,64}$")
_CATEGORIES = frozenset({
    "unit_tests", "integration_tests", "static_analysis",
    "dependency_audit", "secret_scan", "security_tests",
})


class GitHubEvidenceError(RuntimeError):
    """GitHub evidence is absent, stale, incomplete, or untrusted."""


class GitHubActionsEvidenceAdapter:
    """Convert successful, exact-commit GitHub Actions runs into gate evidence.

    workflow_map maps each required category to one or more exact GitHub
    workflow names. Names are intentionally configuration, not fuzzy guesses.
    """

    def __init__(
        self,
        fetch_runs: Callable[[str], list[dict]],
        *,
        workflow_map: Mapping[str, tuple[str, ...]],
        allowed_events: tuple[str, ...] = ("pull_request", "workflow_dispatch", "push"),
    ):
        if not callable(fetch_runs):
            raise TypeError("an authenticated GitHub workflow-run fetcher is required.")
        if set(workflow_map) != _CATEGORIES:
            raise ValueError("workflow_map must define all six required verification categories.")
        normalized = {}
        for category, names in workflow_map.items():
            if not isinstance(names, tuple) or not names:
                raise ValueError("each category must map to a non-empty tuple of exact workflow names.")
            if any(not isinstance(name, str) or not name.strip() or len(name) > 200 for name in names):
                raise ValueError("workflow names must be bounded non-empty strings.")
            if len(set(names)) != len(names):
                raise ValueError("duplicate workflow names are not allowed.")
            normalized[category] = names
        if not isinstance(allowed_events, tuple) or not allowed_events or any(
            not isinstance(x, str) or not x for x in allowed_events
        ):
            raise ValueError("allowed_events must be a non-empty tuple.")
        self.fetch_runs = fetch_runs
        self.workflow_map = normalized
        self.allowed_events = frozenset(allowed_events)

    @staticmethod
    def _parse_time(value: str) -> float:
        if not isinstance(value, str):
            return 0.0
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError):
            return 0.0

    def build_report(self, expected_commit: str) -> VerificationReport:
        if not isinstance(expected_commit, str) or not _SHA.fullmatch(expected_commit):
            raise GitHubEvidenceError("expected commit SHA is invalid.")
        try:
            runs = self.fetch_runs(expected_commit)
        except Exception as exc:
            raise GitHubEvidenceError("authenticated GitHub Actions lookup failed.") from exc
        if not isinstance(runs, list) or len(runs) > 1000:
            raise GitHubEvidenceError("GitHub returned an invalid or oversized workflow-run collection.")

        valid = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            if run.get("head_sha") != expected_commit:
                continue
            if run.get("event") not in self.allowed_events:
                continue
            if not isinstance(run.get("name"), str) or not run["name"].strip():
                continue
            run_id = run.get("id")
            if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
                continue
            attempt = run.get("run_attempt", 1)
            number = run.get("run_number", 0)
            if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
                continue
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                continue
            valid.append(run)

        checks = []
        for category, required_names in sorted(self.workflow_map.items()):
            matches = [r for r in valid if r["name"] in required_names]
            if not matches:
                raise GitHubEvidenceError(f"no trusted workflow run found for required category: {category}")
            # Select the newest attempt for each exact workflow name. A latest
            # failure must not be hidden by an earlier successful run.
            selected = []
            for name in required_names:
                candidates = [r for r in matches if r["name"] == name]
                if not candidates:
                    raise GitHubEvidenceError(f"required workflow missing: {name}")
                candidates.sort(key=lambda r: (r.get("run_number", 0), r.get("run_attempt", 1),
                                               self._parse_time(r.get("updated_at", ""))))
                selected.append(candidates[-1])
            for run in selected:
                if run.get("status") != "completed" or run.get("conclusion") != "success":
                    raise GitHubEvidenceError(
                        f"required workflow has not succeeded on the expected commit: {run['name']}"
                    )
            ids = ",".join(str(r["id"]) for r in selected)
            checks.append(CheckResult(
                name=category,
                commit_sha=expected_commit,
                passed=True,
                evidence_id="gh-" + ids,
            ))

        report = VerificationReport(commit_sha=expected_commit, checks=tuple(checks))
        try:
            EvolutionReviewError  # Keep import explicit for API consumers.
        except NameError:  # pragma: no cover
            raise GitHubEvidenceError("verification contract unavailable.")
        return report
