"""Versioned, immutable annotation rubrics with canonical integrity digests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re

MAX_CRITERIA = 64
MAX_TEXT = 2000
MAX_REVIEWER = 128


class RubricError(ValueError):
    """Raised when a rubric is invalid or its digest does not verify."""


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    criterion_id: str
    description: str
    weight: int


@dataclass(frozen=True, slots=True)
class AnnotationRubric:
    rubric_id: str
    version: int
    title: str
    instructions: str
    criteria: tuple[RubricCriterion, ...]
    created_at: str
    rubric_sha256: str
    provenance: str = "owner_supplied_unverified"
    verified: bool = False


def _digest(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise RubricError("invalid created_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RubricError("invalid created_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RubricError("created_at must include a timezone")
    return value


def _payload(rubric_id, version, title, instructions, criteria, created_at):
    return {
        "rubric_id": rubric_id, "version": version, "title": title,
        "instructions": instructions,
        "criteria": [{"criterion_id": c.criterion_id, "description": c.description, "weight": c.weight} for c in criteria],
        "created_at": created_at, "provenance": "owner_supplied_unverified", "verified": False,
    }


def create_annotation_rubric(
    *, rubric_id: str, version: int, title: str, instructions: str,
    criteria: tuple[RubricCriterion, ...] | list[RubricCriterion], created_at: str,
) -> AnnotationRubric:
    if not isinstance(rubric_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", rubric_id):
        raise RubricError("invalid rubric_id")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RubricError("version must be a positive integer")
    for value, field in ((title, "title"), (instructions, "instructions")):
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
            raise RubricError(f"invalid {field}")
    items = tuple(criteria)
    if not items or len(items) > MAX_CRITERIA or any(not isinstance(c, RubricCriterion) for c in items):
        raise RubricError("criteria must be a non-empty bounded sequence")
    ids = set()
    for criterion in items:
        if not isinstance(criterion.criterion_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", criterion.criterion_id):
            raise RubricError("invalid criterion_id")
        if criterion.criterion_id in ids:
            raise RubricError("duplicate criterion_id")
        ids.add(criterion.criterion_id)
        if not isinstance(criterion.description, str) or not criterion.description.strip() or len(criterion.description) > MAX_TEXT:
            raise RubricError("invalid criterion description")
        if isinstance(criterion.weight, bool) or not isinstance(criterion.weight, int) or not 1 <= criterion.weight <= 100:
            raise RubricError("criterion weight must be an integer from 1 to 100")
    timestamp = _timestamp(created_at)
    payload = _payload(rubric_id, version, title, instructions, items, timestamp)
    return AnnotationRubric(rubric_id, version, title, instructions, items, timestamp, _digest(payload))


def validate_annotation_rubric(rubric: AnnotationRubric) -> bool:
    if not isinstance(rubric, AnnotationRubric) or rubric.provenance != "owner_supplied_unverified" or rubric.verified:
        return False
    try:
        expected = _digest(_payload(rubric.rubric_id, rubric.version, rubric.title, rubric.instructions, rubric.criteria, rubric.created_at))
        _timestamp(rubric.created_at)
    except (RubricError, TypeError, ValueError):
        return False
    return expected == rubric.rubric_sha256
