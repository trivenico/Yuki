"""Deterministic, rubric-configured annotation quality checks.

Quality findings are advisory signals. They never replace human review or
change a submission's review decision.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


_QUALITY_FIELDS = frozenset({
    "minimum_confidence", "require_rationale", "minimum_items",
    "maximum_items", "required_labels",
})


def validate_quality_policy(rubric: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(rubric, Mapping):
        raise ValueError("invalid_project_rubric")
    policy = rubric.get("quality", {})
    if not isinstance(policy, Mapping) or set(policy) - _QUALITY_FIELDS:
        raise ValueError("invalid_quality_policy")
    result = dict(policy)
    if "minimum_confidence" in result:
        value = result["minimum_confidence"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid_quality_minimum_confidence")
        result["minimum_confidence"] = float(value)
    if "require_rationale" in result and type(result["require_rationale"]) is not bool:
        raise ValueError("invalid_quality_require_rationale")
    for key in ("minimum_items", "maximum_items"):
        if key in result and (type(result[key]) is not int or not 0 <= result[key] <= 10000):
            raise ValueError("invalid_quality_" + key)
    if ("minimum_items" in result and "maximum_items" in result
            and result["minimum_items"] > result["maximum_items"]):
        raise ValueError("invalid_quality_item_range")
    if "required_labels" in result:
        labels = result["required_labels"]
        if (not isinstance(labels, list) or len(labels) > 256
                or any(not isinstance(x, str) or not x.strip() or len(x) > 256 for x in labels)
                or len(set(labels)) != len(labels)):
            raise ValueError("invalid_quality_required_labels")
    return result


def _annotation_items(task_type: str, payload: Mapping[str, Any]) -> tuple[int, set[str]]:
    if task_type in {"image_classification", "text_classification"}:
        return 1, {payload["label"]} if isinstance(payload.get("label"), str) else set()
    collection_fields = {
        "bounding_boxes": "boxes", "polygon_segmentation": "polygons",
        "video_tracking": "tracks", "audio_transcription": "segments",
        "speaker_diarization": "segments", "entity_extraction": "entities",
        "response_ranking": "ranking",
    }
    field = collection_fields.get(task_type)
    if field:
        items = payload.get(field, [])
        labels = {
            item.get("label") for item in items
            if isinstance(item, Mapping) and isinstance(item.get("label"), str)
        }
        return len(items) if isinstance(items, list) else 0, labels
    if task_type == "llm_evaluation":
        scores = payload.get("scores", {})
        return len(scores) if isinstance(scores, Mapping) else 0, set()
    return len(payload), set()


def assess_annotation_quality(
    task_type: str, payload: Mapping[str, Any], rubric: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a stable quality status and machine-readable rubric findings."""
    policy = validate_quality_policy(rubric)
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_quality_payload")
    findings: list[dict[str, str]] = []
    count, labels = _annotation_items(task_type, payload)
    if "minimum_confidence" in policy:
        confidence = payload.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
        ):
            findings.append({"code": "confidence_unavailable", "severity": "info",
                             "message": "Confidence could not be evaluated."})
        elif confidence is not None and confidence < policy["minimum_confidence"]:
            findings.append({"code": "confidence_below_threshold", "severity": "warning",
                             "message": "Reported confidence is below the project threshold."})
    if policy.get("require_rationale") and not (
        isinstance(payload.get("rationale"), str) and payload["rationale"].strip()
    ):
        findings.append({"code": "rationale_missing", "severity": "warning",
                         "message": "The project requires a non-empty rationale."})
    if count < policy.get("minimum_items", 0):
        findings.append({"code": "annotation_count_below_minimum", "severity": "warning",
                         "message": "The annotation count is below the project minimum."})
    if count > policy.get("maximum_items", 10000):
        findings.append({"code": "annotation_count_above_maximum", "severity": "warning",
                         "message": "The annotation count exceeds the project maximum."})
    missing = sorted(set(policy.get("required_labels", [])) - labels)
    if missing:
        findings.append({"code": "required_labels_missing", "severity": "warning",
                         "message": "One or more project-required labels are missing."})
    status = "warning" if any(x["severity"] == "warning" for x in findings) else "pass"
    return {
        "status": status,
        "findings": findings,
        "metrics": {"annotation_items": count, "distinct_labels": len(labels)},
        "policy_version": 1,
    }
