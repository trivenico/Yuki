"""Fail-closed orchestration for GitHub checks and durable engineering evidence.

This module does not merge or deploy. Any consumer must treat a blocked decision, an
unpersisted audit record, or an exception as a hard stop for protected operations.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from core.github_check_gate import (
    CheckGateError,
    PullRequestEvidence,
    evaluate_pull_request_checks,
)
from core.github_evidence_client import GitHubEvidenceClient, GitHubEvidenceError
from core.engineering_evidence_store import (
    EngineeringEvidenceError,
    SQLiteEngineeringEvidenceStore,
)

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class ReleaseGateDecision:
    decision: str
    reasons: tuple[str, ...]
    repository: str
    pull_request: int
    expected_head_sha: str
    evidence_id: int | None
    audit_persisted: bool

    @property
    def allowed(self) -> bool:
        """True only when checks passed and their decision was durably recorded."""
        return self.decision == "ready-for-required-checks" and self.audit_persisted


ApprovalVerifier = Callable[[str, str, int, str], bool]


def evaluate_release_gate(
    *,
    client: GitHubEvidenceClient,
    store: SQLiteEngineeringEvidenceStore,
    repository: str,
    pull_request: int,
    expected_head_sha: str,
    required_checks: tuple[str, ...],
    require_human_approval: bool = False,
    approval_verifier: ApprovalVerifier | None = None,
) -> ReleaseGateDecision:
    """Fetch, evaluate, and persist a gate decision. Never authorize on infrastructure failure.

    If human approval is required, the verifier must independently validate the trusted
    GitHub review identity and the exact expected commit SHA. A raw boolean supplied as
    user input is not a valid verifier. This function intentionally has no merge/deploy API.
    """
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise ValueError("repository must use owner/name format")
    owner, repo = repository.split("/", 1)
    if not owner or not repo or any(part in {".", ".."} for part in (owner, repo)):
        raise ValueError("repository must use valid owner/name components")
    if type(pull_request) is not int or pull_request < 1:
        raise ValueError("pull_request must be a positive integer")
    if not isinstance(expected_head_sha, str) or not _SHA_RE.fullmatch(expected_head_sha):
        raise ValueError("expected_head_sha must be a 40-character commit SHA")
    if not isinstance(required_checks, tuple) or len(required_checks) > 200:
        raise ValueError("required_checks must be a bounded tuple")
    if type(require_human_approval) is not bool:
        raise ValueError("require_human_approval must be a boolean")

    expected_sha = expected_head_sha.lower()
    evidence: PullRequestEvidence | None = None
    reasons: list[str] = []
    snapshot: dict = {
        "schema_version": 1,
        "decision": "block",
        "reasons": [],
        "repository": repository.lower(),
        "pull_request": pull_request,
        "expected_head_sha": expected_sha,
        "required_checks": list(required_checks),
        "checks": [],
        "approval_required": require_human_approval,
        "approval_verified": False,
    }

    try:
        evidence = client.fetch_pull_request_evidence(
            owner,
            repo,
            pull_request,
            required_checks=required_checks,
            # Approval must be independently verified below; don't trust client-supplied flags.
            human_approval_required=False,
            human_approved=False,
        )
        if evidence.number != pull_request:
            reasons.append("pull_request_number_mismatch")
        gate = evaluate_pull_request_checks(evidence, expected_head_sha=expected_sha)
        reasons.extend(gate.reasons)
        snapshot["observed_head_sha"] = evidence.head_sha.lower()
        snapshot["pr_state"] = evidence.state
        snapshot["draft"] = evidence.draft
        snapshot["base_branch"] = evidence.base_branch
        snapshot["checks"] = [
            {
                "name": check.name,
                "status": check.status,
                "conclusion": check.conclusion,
                "head_sha": check.head_sha.lower(),
            }
            for check in evidence.checks
        ]
    except (GitHubEvidenceError, CheckGateError, OSError, TimeoutError, ValueError) as exc:
        # Do not persist exception text: it may contain sensitive request details.
        reasons.append("github_evidence_unavailable_or_invalid")
        snapshot["failure_type"] = type(exc).__name__
    except Exception as exc:
        # Fail closed for unexpected adapter failures too; keep only the exception class.
        reasons.append("unexpected_evidence_collection_failure")
        snapshot["failure_type"] = type(exc).__name__

    approval_verified = False
    if require_human_approval:
        if approval_verifier is None:
            reasons.append("trusted_human_approval_verifier_missing")
        else:
            try:
                approval_verified = approval_verifier(owner, repo, pull_request, expected_sha) is True
            except Exception:
                approval_verified = False
            if not approval_verified:
                reasons.append("trusted_human_approval_missing_or_invalid")
    snapshot["approval_verified"] = approval_verified
    reasons = list(dict.fromkeys(reasons))
    decision = "block" if reasons else "ready-for-required-checks"
    snapshot["decision"] = decision
    snapshot["reasons"] = reasons

    evidence_id: int | None = None
    try:
        if store.verify_chain() is not True:
            return ReleaseGateDecision(
                decision="block",
                reasons=tuple(dict.fromkeys(reasons + ["audit_evidence_chain_invalid"])),
                repository=repository.lower(),
                pull_request=pull_request,
                expected_head_sha=expected_sha,
                evidence_id=None,
                audit_persisted=False,
            )
        evidence_id = store.append(
            repository=repository,
            pr_number=pull_request,
            head_sha=expected_sha,
            payload=snapshot,
        )
        if store.verify_chain() is not True:
            return ReleaseGateDecision(
                decision="block",
                reasons=tuple(dict.fromkeys(reasons + ["audit_evidence_chain_invalid_after_append"])),
                repository=repository.lower(),
                pull_request=pull_request,
                expected_head_sha=expected_sha,
                evidence_id=evidence_id,
                audit_persisted=False,
            )
    except (EngineeringEvidenceError, OSError, TimeoutError, ValueError):
        return ReleaseGateDecision(
            decision="block",
            reasons=tuple(dict.fromkeys(reasons + ["audit_evidence_persistence_failed"])),
            repository=repository.lower(),
            pull_request=pull_request,
            expected_head_sha=expected_sha,
            evidence_id=None,
            audit_persisted=False,
        )
    except Exception:
        return ReleaseGateDecision(
            decision="block",
            reasons=tuple(dict.fromkeys(reasons + ["unexpected_audit_persistence_failure"])),
            repository=repository.lower(),
            pull_request=pull_request,
            expected_head_sha=expected_sha,
            evidence_id=None,
            audit_persisted=False,
        )

    return ReleaseGateDecision(
        decision=decision,
        reasons=tuple(reasons),
        repository=repository.lower(),
        pull_request=pull_request,
        expected_head_sha=expected_sha,
        evidence_id=evidence_id,
        audit_persisted=True,
    )
