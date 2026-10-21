"""Validate GitHub PR check evidence before an engineering workflow can proceed.

A trusted adapter must fetch the evidence. This pure evaluator never merges PRs and does
not replace GitHub branch protection.
"""
from __future__ import annotations
from dataclasses import dataclass


class CheckGateError(ValueError):
    """GitHub check evidence is malformed or inconsistent."""


_SUCCESS_CONCLUSIONS = frozenset({"success", "neutral"})
_ALLOWED_CONCLUSIONS = frozenset({
    "success", "neutral", "skipped", "failure", "cancelled", "timed_out",
    "action_required", "startup_failure", "stale",
})


@dataclass(frozen=True)
class GitHubCheck:
    name: str
    status: str
    conclusion: str | None
    head_sha: str

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 300:
            raise CheckGateError("check name is invalid")
        if self.status not in {"queued", "in_progress", "completed"}:
            raise CheckGateError("check status is invalid")
        if self.conclusion is not None and self.conclusion not in _ALLOWED_CONCLUSIONS:
            raise CheckGateError("check conclusion is invalid")
        if not _valid_sha(self.head_sha):
            raise CheckGateError("check head SHA must be a 40-character commit SHA")
        if self.status != "completed" and self.conclusion is not None:
            raise CheckGateError("incomplete check cannot have a conclusion")


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value.lower())


@dataclass(frozen=True)
class PullRequestEvidence:
    number: int
    state: str
    draft: bool
    head_sha: str
    base_branch: str
    checks: tuple[GitHubCheck, ...]
    required_checks: tuple[str, ...]
    human_approval_required: bool = False
    human_approved: bool = False

    def validate(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise CheckGateError("PR number is invalid")
        if self.state not in {"open", "closed"}:
            raise CheckGateError("PR state is invalid")
        if type(self.draft) is not bool:
            raise CheckGateError("draft must be a boolean")
        if not _valid_sha(self.head_sha):
            raise CheckGateError("PR head SHA must be a 40-character commit SHA")
        if not isinstance(self.base_branch, str) or not self.base_branch.strip():
            raise CheckGateError("base branch is required")
        if not isinstance(self.checks, tuple) or len(self.checks) > 2000:
            raise CheckGateError("checks must be a bounded tuple")
        if not isinstance(self.required_checks, tuple) or len(self.required_checks) > 200:
            raise CheckGateError("required checks must be a bounded tuple")
        for check in self.checks:
            if not isinstance(check, GitHubCheck):
                raise CheckGateError("check entry has an invalid type")
            check.validate()
        for name in self.required_checks:
            if not isinstance(name, str) or not name.strip() or len(name) > 300:
                raise CheckGateError("required check names must be non-empty and bounded")
        if type(self.human_approval_required) is not bool or type(self.human_approved) is not bool:
            raise CheckGateError("approval flags must be booleans")


@dataclass(frozen=True)
class CheckGateResult:
    decision: str
    reasons: tuple[str, ...]
    head_sha: str


def evaluate_pull_request_checks(evidence: PullRequestEvidence, *, expected_head_sha: str) -> CheckGateResult:
    """Fail closed unless each required check passed for the exact expected SHA."""
    evidence.validate()
    if not _valid_sha(expected_head_sha):
        raise CheckGateError("expected head SHA is invalid")
    expected = expected_head_sha.lower()
    reasons: list[str] = []
    if evidence.head_sha.lower() != expected:
        reasons.append("stale_pull_request_head")
    if evidence.state != "open":
        reasons.append("pull_request_not_open")
    if evidence.draft:
        reasons.append("pull_request_is_draft")
    if evidence.human_approval_required and not evidence.human_approved:
        reasons.append("human_approval_required")

    by_name: dict[str, list[GitHubCheck]] = {}
    for check in evidence.checks:
        by_name.setdefault(check.name, []).append(check)
        if check.head_sha.lower() != expected:
            reasons.append(f"stale_check:{check.name}")
        if check.status != "completed":
            reasons.append(f"check_not_completed:{check.name}")
        elif check.conclusion not in _SUCCESS_CONCLUSIONS:
            reasons.append(f"check_not_successful:{check.name}")

    for name, group in by_name.items():
        signatures = {(c.status, c.conclusion, c.head_sha.lower()) for c in group}
        if len(signatures) > 1:
            reasons.append(f"conflicting_duplicate_check:{name}")

    for required in evidence.required_checks:
        group = by_name.get(required, [])
        if not group:
            reasons.append(f"required_check_missing:{required}")
        elif not any(c.status == "completed" and c.conclusion == "success" and c.head_sha.lower() == expected for c in group):
            reasons.append(f"required_check_not_passing:{required}")

    reasons = list(dict.fromkeys(reasons))
    return CheckGateResult(
        decision="block" if reasons else "ready-for-required-checks",
        reasons=tuple(reasons),
        head_sha=expected,
    )
