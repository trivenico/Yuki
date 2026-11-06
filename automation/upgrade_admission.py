"""Admission bridge from human-reviewed proposals to the durable execution queue.

The bridge validates review status and immutable snapshot identity before
persisting an execution request. It does not execute tests or provide isolation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from automation.authorized_read_tools import ToolAuthorizationError
from automation.self_upgrade_pipeline import SelfUpgradeReviewStore
from automation.upgrade_execution_contracts import ExecutionRequest
from automation.upgrade_execution_store import UpgradeExecutionRecord, UpgradeExecutionStore


def admit_approved_upgrade(
    review_store: SelfUpgradeReviewStore,
    execution_store: UpgradeExecutionStore,
    proposal_id: str,
    *,
    requesting_principal: str,
    reviewer: str,
    approval_id: str,
    idempotency_key: str,
    execution_id: str,
    test_targets: Sequence[str],
    runner_kind: str,
    runner_image_digest: str,
    architecture: str,
    runtime_version: str,
    policy_digest: str,
) -> UpgradeExecutionRecord:
    """Admit only the exact snapshot approved by a distinct reviewer."""
    if not isinstance(review_store, SelfUpgradeReviewStore):
        raise TypeError("review_store_must_be_SelfUpgradeReviewStore")
    if not isinstance(execution_store, UpgradeExecutionStore):
        raise TypeError("execution_store_must_be_UpgradeExecutionStore")
    if not isinstance(requesting_principal, str) or not requesting_principal.strip():
        raise ValueError("requesting_principal_invalid")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer_invalid")
    if requesting_principal.strip() == reviewer.strip():
        raise ToolAuthorizationError("independent_reviewer_required")

    # This call checks approved status, exact reviewer identity, and snapshot
    # integrity before any execution record is created.
    snapshot = review_store.get_approved_snapshot(
        proposal_id, reviewer=reviewer.strip(),
    )
    snapshot_json = snapshot.get("snapshot_json")
    content_digest = snapshot.get("content_digest")
    if not isinstance(snapshot_json, str) or not isinstance(content_digest, str):
        raise ValueError("approved_snapshot_metadata_missing")
    object_digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    if object_digest != content_digest:
        raise ValueError("approved_snapshot_digest_mismatch")

    request = ExecutionRequest.create(
        proposal_id=proposal_id,
        snapshot_digest=content_digest,
        snapshot_object_digest=object_digest,
        snapshot_version=content_digest,
        test_targets=test_targets,
        runner_kind=runner_kind,
        runner_image_digest=runner_image_digest,
        architecture=architecture,
        runtime_version=runtime_version,
        policy_digest=policy_digest,
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        requesting_principal=requesting_principal.strip(),
        reviewer=reviewer.strip(),
        approval_id=approval_id,
    )
    return execution_store.create(request)
