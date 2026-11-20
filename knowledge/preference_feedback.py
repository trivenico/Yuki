"""Bounded, digest-bound pairwise preference feedback; never trains a model."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re

from knowledge.annotation_rubric import AnnotationRubric, validate_annotation_rubric

MAX_TEXT_CHARS = 32_768
MAX_RATIONALE_CHARS = 2000
MAX_REVIEWER_CHARS = 128
PREFERENCES = frozenset({"a", "b", "tie", "neither"})


class PreferenceFeedbackError(ValueError):
    """Raised when preference feedback is invalid or its source binding fails."""


@dataclass(frozen=True, slots=True)
class PreferenceFeedback:
    comparison_id: str
    prompt_sha256: str
    response_a_sha256: str
    response_b_sha256: str
    rubric_sha256: str
    preference: str
    reviewer_label: str
    rationale: str
    reviewed_at: str
    feedback_sha256: str
    provenance: str = "reviewer_supplied_unverified"
    verified: bool = False


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_CHARS:
        raise PreferenceFeedbackError(f"invalid {field}")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise PreferenceFeedbackError("invalid reviewed_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreferenceFeedbackError("invalid reviewed_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PreferenceFeedbackError("reviewed_at must include a timezone")
    return value


def create_preference_feedback(
    *, comparison_id: str, prompt: str, response_a: str, response_b: str,
    rubric: AnnotationRubric, preference: str, reviewer_label: str,
    rationale: str, reviewed_at: str,
) -> PreferenceFeedback:
    if not isinstance(comparison_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", comparison_id):
        raise PreferenceFeedbackError("invalid comparison_id")
    if not validate_annotation_rubric(rubric):
        raise PreferenceFeedbackError("rubric is invalid or its digest does not match")
    if preference not in PREFERENCES:
        raise PreferenceFeedbackError("preference must be a, b, tie, or neither")
    if not isinstance(reviewer_label, str) or not reviewer_label.strip() or len(reviewer_label) > MAX_REVIEWER_CHARS:
        raise PreferenceFeedbackError("invalid reviewer_label")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > MAX_RATIONALE_CHARS:
        raise PreferenceFeedbackError("invalid rationale")
    prompt_sha = _text_digest(prompt, "prompt")
    response_a_sha = _text_digest(response_a, "response_a")
    response_b_sha = _text_digest(response_b, "response_b")
    if response_a_sha == response_b_sha:
        raise PreferenceFeedbackError("candidate responses must differ")
    timestamp = _timestamp(reviewed_at)
    payload = {
        "comparison_id": comparison_id, "prompt_sha256": prompt_sha,
        "response_a_sha256": response_a_sha, "response_b_sha256": response_b_sha,
        "rubric_sha256": rubric.rubric_sha256, "preference": preference,
        "reviewer_label": reviewer_label, "rationale": rationale,
        "reviewed_at": timestamp, "provenance": "reviewer_supplied_unverified", "verified": False,
    }
    return PreferenceFeedback(
        comparison_id, prompt_sha, response_a_sha, response_b_sha, rubric.rubric_sha256,
        preference, reviewer_label, rationale, timestamp, _digest(payload),
    )


def validate_preference_feedback(record: PreferenceFeedback, rubric: AnnotationRubric) -> bool:
    if not isinstance(record, PreferenceFeedback) or not validate_annotation_rubric(rubric):
        return False
    if record.provenance != "reviewer_supplied_unverified" or record.verified:
        return False
    if record.rubric_sha256 != rubric.rubric_sha256 or record.preference not in PREFERENCES:
        return False
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", record.comparison_id):
        return False
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in (
        record.prompt_sha256, record.response_a_sha256, record.response_b_sha256
    )) or record.response_a_sha256 == record.response_b_sha256:
        return False
    if not isinstance(record.reviewer_label, str) or not record.reviewer_label.strip() or len(record.reviewer_label) > MAX_REVIEWER_CHARS:
        return False
    if not isinstance(record.rationale, str) or not record.rationale.strip() or len(record.rationale) > MAX_RATIONALE_CHARS:
        return False
    try:
        _timestamp(record.reviewed_at)
    except PreferenceFeedbackError:
        return False
    payload = {
        "comparison_id": record.comparison_id, "prompt_sha256": record.prompt_sha256,
        "response_a_sha256": record.response_a_sha256, "response_b_sha256": record.response_b_sha256,
        "rubric_sha256": record.rubric_sha256, "preference": record.preference,
        "reviewer_label": record.reviewer_label, "rationale": record.rationale,
        "reviewed_at": record.reviewed_at, "provenance": record.provenance, "verified": False,
    }
    return record.feedback_sha256 == _digest(payload)
