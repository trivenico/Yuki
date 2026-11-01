"""Collect bounded evidence from Yuki's engineering workflow result objects.

Evidence is provenance-labelled, digest-bound and explicit about its limits.
Caller-provided metadata is never promoted to verified CI/Git provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from automation.engineering_workflow import EngineeringWorkflowResult


_SHA = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class EngineeringEvidenceBundle:
    workflow_id: str
    actor_id: str
    commit_sha: str
    artifact: dict[str, Any]
    evidence: tuple[dict[str, str], ...]
    tests_passed: bool
    evidence_sha256: str
    provenance: str = "yuki_engineering_workflow_result"


class EvidenceCollectionError(ValueError):
    """Evidence was missing, malformed or exceeded collection limits."""


def _text(value: Any, field: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise EvidenceCollectionError(f"{field} must be bounded non-empty text.")
    return value.strip()


def collect_engineering_evidence(
    result: EngineeringWorkflowResult, *, actor_id: str, commit_sha: str,
) -> EngineeringEvidenceBundle:
    if not isinstance(result, EngineeringWorkflowResult):
        raise EvidenceCollectionError("a real EngineeringWorkflowResult is required.")
    actor = _text(actor_id, "actor_id", 200)
    workflow_id = _text(result.workflow_id, "workflow_id", 200)
    if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
        raise EvidenceCollectionError("commit_sha must be a 40-64 character lowercase Git SHA.")
    if result.outcome not in {"succeeded", "tests_failed"}:
        raise EvidenceCollectionError("unsupported engineering workflow outcome.")
    changed_paths = result.change_result.changed_paths
    if not isinstance(changed_paths, tuple) or len(changed_paths) > 80:
        raise EvidenceCollectionError("changed paths must be a bounded tuple.")
    paths = [_text(path, "changed_path", 500) for path in changed_paths]
    if len(set(paths)) != len(paths):
        raise EvidenceCollectionError("changed paths must be unique.")
    if result.change_result.actor_id != actor:
        raise EvidenceCollectionError("evidence actor does not match the change actor.")
    if not re.fullmatch(r"[0-9a-f]{64}", result.change_result.content_digest or ""):
        raise EvidenceCollectionError("change result digest is invalid.")
    test = result.test_result
    if type(test.exit_code) is not int or type(test.timed_out) is not bool:
        raise EvidenceCollectionError("test result status fields are invalid.")
    if not isinstance(test.duration_seconds, (int, float)) or test.duration_seconds < 0:
        raise EvidenceCollectionError("test duration is invalid.")
    stdout = _text(test.stdout or "(empty)", "test stdout", 2000)
    stderr = _text(test.stderr or "(empty)", "test stderr", 2000)
    tests_passed = test.exit_code == 0 and not test.timed_out
    expected_outcome = "succeeded" if tests_passed else "tests_failed"
    if result.outcome != expected_outcome:
        raise EvidenceCollectionError("workflow outcome does not match the actual test result.")
    artifact = {
        "workflow_id": workflow_id,
        "commit_sha": commit_sha,
        "changed_paths": paths,
        "change_digest": result.change_result.content_digest,
        "test_target": _text(test.target, "test target", 200),
        "outcome": result.outcome,
    }
    records = (
        {
            "kind": "workspace_change",
            "description": "Approved workspace change result returned by Yuki engineering workflow.",
            "result": f"actor={actor}; paths={len(paths)}; content_sha256={result.change_result.content_digest}",
            "reference": workflow_id,
        },
        {
            "kind": "test_execution",
            "description": f"Allow-listed test target {test.target}; exit={test.exit_code}; timeout={test.timed_out}; duration={float(test.duration_seconds):.3f}s.",
            "result": "passed" if tests_passed else "failed",
            "reference": "stdout_sha256=" + hashlib.sha256(stdout.encode()).hexdigest()
                + "; stderr_sha256=" + hashlib.sha256(stderr.encode()).hexdigest(),
        },
    )
    canonical = json.dumps(
        {"artifact": artifact, "evidence": records, "tests_passed": tests_passed},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return EngineeringEvidenceBundle(
        workflow_id=workflow_id, actor_id=actor, commit_sha=commit_sha,
        artifact=artifact, evidence=records, tests_passed=tests_passed,
        evidence_sha256=hashlib.sha256(canonical).hexdigest(),
    )
