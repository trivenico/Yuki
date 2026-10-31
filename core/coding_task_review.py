"""Bind independent static review evidence to a versioned coding task.

The caller must provide the exact post-patch source snapshot from a trusted,
isolated checkout. This module does not apply patches or execute candidate code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

from core.coding_tasks import CodingTaskConflict, CodingTaskStore, CodingTaskTransitionError
from core.independent_code_review import ReviewReport, review_python_changes, report_to_dict


class CodingTaskReviewError(RuntimeError):
    """A coding task could not be reviewed or advanced safely."""


def review_coding_task_changes(
    store: CodingTaskStore,
    *,
    task_id: str,
    expected_version: int,
    expected_patch_sha256: str,
    proposed_files: Mapping[str, str],
) -> tuple[ReviewReport, object]:
    """Review exact proposed file contents and bind the report to the task patch digest.

    A clean static report only advances draft -> ready_for_review. A human must
    still inspect the patch and evidence before the existing approval transition.
    """
    if not isinstance(expected_patch_sha256, str) or len(expected_patch_sha256) != 64:
        raise ValueError("expected_patch_sha256 must be a SHA-256 hex digest")
    try:
        int(expected_patch_sha256, 16)
    except ValueError as exc:
        raise ValueError("expected_patch_sha256 must be a SHA-256 hex digest") from exc

    try:
        task = store.get(task_id)
    except (KeyError, ValueError) as exc:
        raise CodingTaskReviewError("coding task does not exist or is invalid") from exc
    if task.version != expected_version:
        raise CodingTaskConflict("task version changed; reload before reviewing")
    if task.state != "draft":
        raise CodingTaskReviewError("only draft coding tasks can enter static review")
    if not task.patch or not task.patch_sha256 or not store.patch_integrity_ok(task_id):
        raise CodingTaskReviewError("task must contain an intact proposed patch")
    if task.patch_sha256 != expected_patch_sha256:
        raise CodingTaskConflict("proposed patch digest differs from the reviewed task version")

    report = review_python_changes(proposed_files)
    review_record = {
        "reviewer": "independent-python-review",
        "reviewer_version": 1,
        "task_id": task_id,
        "task_version": expected_version,
        "patch_sha256": task.patch_sha256,
        "source_snapshot_sha256": hashlib.sha256(
            json.dumps(dict(proposed_files), sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest(),
        "report": report_to_dict(report),
        "merge_authorized": False,
    }
    serialized = json.dumps(review_record, sort_keys=True, ensure_ascii=True)
    if len(serialized) > 7_500:
        raise CodingTaskReviewError("review evidence exceeds the bounded task review storage limit")

    if not report.eligible_for_human_review:
        try:
            updated = store.attach_review_evidence(
                task_id, expected_version=expected_version, review=serialized,
            )
        except (CodingTaskConflict, CodingTaskTransitionError) as exc:
            raise CodingTaskReviewError("task changed before blocking review evidence could be attached") from exc
        return report, updated

    try:
        updated = store.transition(
            task_id,
            expected_version=expected_version,
            new_state="ready_for_review",
            review=serialized,
        )
    except (CodingTaskConflict, CodingTaskTransitionError) as exc:
        raise CodingTaskReviewError("task changed before review evidence could be attached") from exc
    return report, updated
