"""Read-only adapters for bounded Label Studio and doccano exports."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
from typing import Any

from knowledge.annotation import (
    AnnotationDataset,
    AnnotationImportError,
    MAX_INPUT_BYTES,
    MAX_RECORDS,
    import_annotation_jsonl,
)

MAX_TASKS = 10_000
MAX_RESULTS_PER_TASK = 256
MAX_LABEL_CHARS = 256


class AnnotationAdapterError(ValueError):
    """Raised when a vendor export is malformed or outside the supported profile."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(payload: bytes, *, max_bytes: int) -> Any:
    if not isinstance(payload, bytes) or len(payload) > max_bytes:
        raise AnnotationAdapterError("export must be bytes within the configured size limit")
    try:
        return json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AnnotationAdapterError("invalid JSON export") from exc


def _identifier(value: Any, field: str) -> str:
    text = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", text):
        raise AnnotationAdapterError(f"invalid {field}")
    return text


def _label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_LABEL_CHARS:
        raise AnnotationAdapterError("invalid label")
    return value


def _normalized_dataset(
    rows: list[dict[str, Any]],
    *,
    source_format: str,
    source_label: str,
    imported_at: str,
    skipped: int = 0,
) -> AnnotationDataset:
    payload = b"\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        for row in rows
    )
    try:
        dataset = import_annotation_jsonl(
            payload,
            source_format=source_format,
            source_label=source_label,
            imported_at=imported_at,
        )
    except AnnotationImportError as exc:
        raise AnnotationAdapterError(str(exc)) from exc
    return replace(dataset, skipped_records=skipped)


def import_label_studio_export(
    payload: bytes,
    *,
    source_label: str,
    imported_at: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> AnnotationDataset:
    """Import a Label Studio JSON task export's supported result types.

    Supported result value keys: choices, rectanglelabels, polygonlabels,
    and labels with text start/end offsets. Unsupported result types are
    counted as skipped; malformed supported results reject the whole import.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_INPUT_BYTES:
        raise AnnotationAdapterError("invalid max_bytes")
    tasks = _load_json(payload, max_bytes=max_bytes)
    if not isinstance(tasks, list) or len(tasks) > MAX_TASKS:
        raise AnnotationAdapterError("Label Studio export must be a bounded task array")
    rows: list[dict[str, Any]] = []
    skipped = 0
    seen_tasks: set[str] = set()
    for task_index, task in enumerate(tasks):
        if not isinstance(task, dict) or "id" not in task:
            raise AnnotationAdapterError("each Label Studio task must contain an id")
        task_id = _identifier(task["id"], "task id")
        if task_id in seen_tasks:
            raise AnnotationAdapterError("duplicate Label Studio task id")
        seen_tasks.add(task_id)
        annotations = task.get("annotations", [])
        if not isinstance(annotations, list) or len(annotations) > MAX_RESULTS_PER_TASK:
            raise AnnotationAdapterError("invalid Label Studio annotations list")
        for annotation_index, annotation in enumerate(annotations):
            if not isinstance(annotation, dict):
                raise AnnotationAdapterError("invalid Label Studio annotation")
            if annotation.get("was_cancelled") is True or annotation.get("skipped") is True:
                skipped += 1
                continue
            results = annotation.get("result", [])
            if not isinstance(results, list) or len(results) > MAX_RESULTS_PER_TASK:
                raise AnnotationAdapterError("invalid Label Studio result list")
            reviewer_id = annotation.get("id", annotation_index)
            for result_index, result in enumerate(results):
                if not isinstance(result, dict) or not isinstance(result.get("value"), dict):
                    raise AnnotationAdapterError("invalid Label Studio result")
                value = result["value"]
                result_type = result.get("type")
                base_id = f"ls-{task_id}-{_identifier(reviewer_id, 'annotation id')}-{result_index}"
                asset_id = f"label-studio-task-{task_id}"
                if result_type == "choices":
                    choices = value.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise AnnotationAdapterError("choices result requires labels")
                    for choice_index, choice in enumerate(choices):
                        rows.append({
                            "annotation_id": f"{base_id}-{choice_index}",
                            "task_type": "classification",
                            "label": _label(choice),
                            "asset_id": asset_id,
                        })
                elif result_type == "rectanglelabels":
                    labels = value.get("rectanglelabels")
                    geometry = [value.get("x"), value.get("y"), value.get("width"), value.get("height")]
                    if not isinstance(labels, list) or not labels:
                        raise AnnotationAdapterError("rectangle result requires labels")
                    for label_index, label in enumerate(labels):
                        rows.append({
                            "annotation_id": f"{base_id}-{label_index}",
                            "task_type": "bbox",
                            "label": _label(label),
                            "asset_id": asset_id,
                            "geometry": [float(number) / 100.0 for number in geometry],
                        })
                elif result_type == "polygonlabels":
                    labels = value.get("polygonlabels")
                    points = value.get("points")
                    if not isinstance(labels, list) or not labels or not isinstance(points, list):
                        raise AnnotationAdapterError("polygon result requires labels and points")
                    geometry: list[float] = []
                    for point in points:
                        if not isinstance(point, list) or len(point) != 2:
                            raise AnnotationAdapterError("invalid polygon point")
                        geometry.extend((float(point[0]) / 100.0, float(point[1]) / 100.0))
                    for label_index, label in enumerate(labels):
                        rows.append({
                            "annotation_id": f"{base_id}-{label_index}",
                            "task_type": "polygon",
                            "label": _label(label),
                            "asset_id": asset_id,
                            "geometry": geometry,
                        })
                elif result_type in {"labels", "textarea"} and "start" in value and "end" in value:
                    text = task.get("data", {}).get("text") if isinstance(task.get("data"), dict) else None
                    if not isinstance(text, str):
                        text = value.get("text")
                    start, end = value.get("start"), value.get("end")
                    labels = value.get("labels")
                    if not isinstance(text, str) or not isinstance(labels, list) or not labels:
                        raise AnnotationAdapterError("text span requires source text and labels")
                    for label_index, label in enumerate(labels):
                        rows.append({
                            "annotation_id": f"{base_id}-{label_index}",
                            "task_type": "text_span",
                            "label": _label(label),
                            "asset_id": asset_id,
                            "text_start": start,
                            "text_end": end,
                            "text_value": text,
                        })
                else:
                    skipped += 1
                if len(rows) > MAX_RECORDS:
                    raise AnnotationAdapterError("normalized annotation count exceeds limit")
    return _normalized_dataset(
        rows, source_format="label-studio-json", source_label=source_label,
        imported_at=imported_at, skipped=skipped,
    )


def import_doccano_export(
    payload: bytes,
    *,
    source_label: str,
    imported_at: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> AnnotationDataset:
    """Import doccano JSONL sequence-label exports as text-span annotations."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_INPUT_BYTES:
        raise AnnotationAdapterError("invalid max_bytes")
    if not isinstance(payload, bytes) or len(payload) > max_bytes:
        raise AnnotationAdapterError("export must be bytes within the configured size limit")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        lines = payload.splitlines()
        for line_number, raw_line in enumerate(lines, start=1):
            if not raw_line.strip():
                continue
            if len(raw_line) > 32 * 1024:
                raise AnnotationAdapterError("doccano record exceeds line limit")
            record = json.loads(raw_line, object_pairs_hook=_unique_object)
            if not isinstance(record, dict) or not isinstance(record.get("text"), str):
                raise AnnotationAdapterError("doccano record requires text")
            record_id = _identifier(record.get("id", line_number), "doccano record id")
            if record_id in seen_ids:
                raise AnnotationAdapterError("duplicate doccano record id")
            seen_ids.add(record_id)
            labels = record.get("label")
            if not isinstance(labels, list) or len(labels) > 1024:
                raise AnnotationAdapterError("doccano label must be a bounded list")
            for label_index, item in enumerate(labels):
                if not isinstance(item, list) or len(item) != 3:
                    raise AnnotationAdapterError("doccano span must be [start, end, label]")
                start, end, label = item
                rows.append({
                    "annotation_id": f"doccano-{record_id}-{label_index}",
                    "task_type": "text_span",
                    "label": _label(label),
                    "asset_id": f"doccano-record-{record_id}",
                    "text_start": start,
                    "text_end": end,
                    "text_value": record["text"],
                })
                if len(rows) > MAX_RECORDS:
                    raise AnnotationAdapterError("normalized annotation count exceeds limit")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        if isinstance(exc, AnnotationAdapterError):
            raise
        raise AnnotationAdapterError("invalid doccano JSONL export") from exc
    return _normalized_dataset(
        rows, source_format="doccano-jsonl", source_label=source_label,
        imported_at=imported_at,
    )
