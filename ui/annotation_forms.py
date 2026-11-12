"""Task-family form metadata and safe conversion into backend annotation payloads.

This module describes presentation fields only. The canonical schema and rubric
checks remain in core.annotation_validation.validate_annotation_payload.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from core.annotation_validation import validate_annotation_payload


_MAX_EDITOR_FIELD_CHARS = 64 * 1024


@dataclass(frozen=True)
class AnnotationEditorField:
    """A UI-neutral field descriptor consumed by terminal and desktop clients."""

    name: str
    label: str
    kind: str
    required: bool
    help_text: str = ""
    options: tuple[str, ...] = ()
    placeholder: str = ""


def annotation_editor_fields(
    task_type: str, rubric: Mapping[str, Any] | None = None,
) -> tuple[AnnotationEditorField, ...]:
    """Return the task-family form layout without duplicating backend validation."""
    rules = rubric or {}
    labels = rules.get("labels", [])
    choices = tuple(v for v in labels if isinstance(v, str) and v.strip()) if isinstance(labels, list) else ()
    label_field = AnnotationEditorField(
        "label", "Label", "choice" if choices else "text", True,
        "Choose a label from the project rubric.", choices,
    )
    common_text = AnnotationEditorField(
        "rationale", "Rationale", "text", False,
        "Optional explanation for your annotation.",
    )
    layouts = {
        "image_classification": (
            label_field,
            AnnotationEditorField("confidence", "Confidence", "number", False,
                                   "Optional confidence from 0 to 1."),
            common_text,
        ),
        "text_classification": (
            label_field,
            AnnotationEditorField("confidence", "Confidence", "number", False,
                                   "Optional confidence from 0 to 1."),
            common_text,
        ),
        "bounding_boxes": (
            AnnotationEditorField("boxes", "Bounding boxes", "json-list", True,
                'JSON array of {label,x,y,width,height}; coordinates are normalized 0–1.'),
        ),
        "polygon_segmentation": (
            AnnotationEditorField("polygons", "Polygons", "json-list", True,
                'JSON array of {label,points}; each point is [x,y] in normalized 0–1 space.'),
        ),
        "video_tracking": (
            AnnotationEditorField("tracks", "Tracks", "json-list", True,
                'JSON array of {track_id,label,frames}; each frame has frame,x,y,width,height.'),
        ),
        "audio_transcription": (
            AnnotationEditorField("segments", "Transcript segments", "json-list", True,
                'JSON array of {start,end,text}; times are seconds.'),
        ),
        "speaker_diarization": (
            AnnotationEditorField("segments", "Speaker segments", "json-list", True,
                'JSON array of {start,end,text,speaker}; times are seconds.'),
        ),
        "entity_extraction": (
            AnnotationEditorField("entities", "Entities", "json-list", True,
                'JSON array of {text,label,start,end}; offsets are character indexes.'),
        ),
        "response_ranking": (
            AnnotationEditorField("ranking", "Ranking", "json-list", True,
                'JSON array of {response_id,rank}; ranks must be unique and contiguous from 1.'),
            common_text,
        ),
        "llm_evaluation": (
            AnnotationEditorField("scores", "Criterion scores", "json-object", True,
                "JSON object mapping criterion names to scores from 0 to 5."),
            common_text,
        ),
        "custom": (
            AnnotationEditorField("payload", "Custom payload", "json-object", True,
                                   "Project-specific JSON object."),
        ),
    }
    try:
        return layouts[task_type]
    except KeyError:
        raise ValueError("unsupported_annotation_task_type") from None


def _json_field(value: Any, name: str, *, expected: type) -> Any:
    if not isinstance(value, str):
        raise ValueError("invalid_editor_field_" + name)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid_editor_field_" + name) from None
    if len(encoded) > _MAX_EDITOR_FIELD_CHARS:
        raise ValueError("invalid_editor_field_" + name)
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("invalid_editor_json_" + name) from None
    if not isinstance(parsed, expected):
        raise ValueError("invalid_editor_json_type_" + name)
    return parsed


def build_annotation_payload(
    task_type: str, values: Mapping[str, Any],
    rubric: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert form values to the canonical payload and run backend validation.

    JSON is used only for repeatable/nested structures. Scalar fields are parsed
    as typed values, and no task data is submitted or persisted by this function.
    """
    if not isinstance(values, Mapping):
        raise ValueError("invalid_editor_values")
    fields = annotation_editor_fields(task_type, rubric)
    allowed = {field.name for field in fields}
    if set(values) - allowed:
        raise ValueError("unexpected_editor_field")
    payload: dict[str, Any] = {}
    for field in fields:
        raw = values.get(field.name, "")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise ValueError("invalid_editor_field_" + field.name)
        raw = raw.strip()
        if not raw:
            if field.required:
                raise ValueError("required_editor_field_" + field.name)
            continue
        if field.kind == "choice" or field.kind == "text":
            payload[field.name] = raw
        elif field.kind == "number":
            try:
                number = float(raw)
            except ValueError:
                raise ValueError("invalid_editor_number_" + field.name) from None
            if not math.isfinite(number):
                raise ValueError("invalid_editor_number_" + field.name)
            payload[field.name] = number
        elif field.kind == "json-list":
            payload[field.name] = _json_field(raw, field.name, expected=list)
        elif field.kind == "json-object":
            parsed = _json_field(raw, field.name, expected=dict)
            if field.name == "payload":
                payload = parsed
            else:
                payload[field.name] = parsed
    validate_annotation_payload(task_type, payload, rubric)
    return payload
