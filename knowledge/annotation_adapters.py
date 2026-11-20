"""Read-only, bounded adapters for Label Studio, doccano, CVAT XML, and VIA JSON."""
from __future__ import annotations

from dataclasses import replace
import json
import math
import re
from typing import Any
import xml.etree.ElementTree as ET

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
MAX_XML_ELEMENTS = 100_000


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


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise AnnotationAdapterError(f"{field} must be numeric")
    try:
        number = float(value)
    except ValueError as exc:
        raise AnnotationAdapterError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise AnnotationAdapterError(f"{field} must be finite")
    return number


def _percent(value: Any) -> float:
    return _number(value, "geometry percentage") / 100.0


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
            payload, source_format=source_format, source_label=source_label,
            imported_at=imported_at,
        )
    except AnnotationImportError as exc:
        raise AnnotationAdapterError(str(exc)) from exc
    return replace(dataset, skipped_records=skipped)


def _limits(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_INPUT_BYTES:
        raise AnnotationAdapterError("invalid max_bytes")


def import_label_studio_export(
    payload: bytes, *, source_label: str, imported_at: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> AnnotationDataset:
    """Import supported Label Studio choices, rectangles, polygons, and text spans."""
    _limits(max_bytes)
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
                value, result_type = result["value"], result.get("type")
                base_id = f"ls-{task_id}-{_identifier(reviewer_id, 'annotation id')}-{result_index}"
                asset_id = f"label-studio-task-{task_id}"
                if result_type == "choices":
                    choices = value.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise AnnotationAdapterError("choices result requires labels")
                    for i, choice in enumerate(choices):
                        rows.append({"annotation_id": f"{base_id}-{i}", "task_type": "classification", "label": _label(choice), "asset_id": asset_id})
                elif result_type == "rectanglelabels":
                    labels = value.get("rectanglelabels")
                    geometry = [_percent(value.get(key)) for key in ("x", "y", "width", "height")]
                    if not isinstance(labels, list) or not labels:
                        raise AnnotationAdapterError("rectangle result requires labels")
                    for i, label in enumerate(labels):
                        rows.append({"annotation_id": f"{base_id}-{i}", "task_type": "bbox", "label": _label(label), "asset_id": asset_id, "geometry": geometry})
                elif result_type == "polygonlabels":
                    labels, points = value.get("polygonlabels"), value.get("points")
                    if not isinstance(labels, list) or not labels or not isinstance(points, list):
                        raise AnnotationAdapterError("polygon result requires labels and points")
                    geometry: list[float] = []
                    for point in points:
                        if not isinstance(point, list) or len(point) != 2:
                            raise AnnotationAdapterError("invalid polygon point")
                        geometry.extend((_percent(point[0]), _percent(point[1])))
                    for i, label in enumerate(labels):
                        rows.append({"annotation_id": f"{base_id}-{i}", "task_type": "polygon", "label": _label(label), "asset_id": asset_id, "geometry": geometry})
                elif result_type in {"labels", "textarea"} and "start" in value and "end" in value:
                    data = task.get("data")
                    text = data.get("text") if isinstance(data, dict) else None
                    if not isinstance(text, str):
                        text = value.get("text")
                    labels = value.get("labels")
                    if not isinstance(text, str) or not isinstance(labels, list) or not labels:
                        raise AnnotationAdapterError("text span requires source text and labels")
                    for i, label in enumerate(labels):
                        rows.append({"annotation_id": f"{base_id}-{i}", "task_type": "text_span", "label": _label(label), "asset_id": asset_id, "text_start": value["start"], "text_end": value["end"], "text_value": text})
                else:
                    skipped += 1
                if len(rows) > MAX_RECORDS:
                    raise AnnotationAdapterError("normalized annotation count exceeds limit")
    return _normalized_dataset(rows, source_format="label-studio-json", source_label=source_label, imported_at=imported_at, skipped=skipped)


def import_doccano_export(
    payload: bytes, *, source_label: str, imported_at: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> AnnotationDataset:
    """Import doccano JSONL sequence-label exports as text-span annotations."""
    _limits(max_bytes)
    if not isinstance(payload, bytes) or len(payload) > max_bytes:
        raise AnnotationAdapterError("export must be bytes within the configured size limit")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        lines = payload.splitlines()
        if len(lines) > MAX_TASKS:
            raise AnnotationAdapterError("doccano record count exceeds limit")
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
                rows.append({"annotation_id": f"doccano-{record_id}-{label_index}", "task_type": "text_span", "label": _label(label), "asset_id": f"doccano-record-{record_id}", "text_start": start, "text_end": end, "text_value": record["text"]})
                if len(rows) > MAX_RECORDS:
                    raise AnnotationAdapterError("normalized annotation count exceeds limit")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        if isinstance(exc, AnnotationAdapterError):
            raise
        raise AnnotationAdapterError("invalid doccano JSONL export") from exc
    return _normalized_dataset(rows, source_format="doccano-jsonl", source_label=source_label, imported_at=imported_at)


def import_cvat_xml_export(
    payload: bytes, *, source_label: str, imported_at: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> AnnotationDataset:
    """Import CVAT image-task XML boxes and polygons; tracks/masks are skipped."""
    _limits(max_bytes)
    if not isinstance(payload, bytes) or len(payload) > max_bytes:
        raise AnnotationAdapterError("export must be bytes within the configured size limit")
    if re.search(br"<!\s*(DOCTYPE|ENTITY)", payload, re.IGNORECASE):
        raise AnnotationAdapterError("DTD and entity declarations are not supported")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise AnnotationAdapterError("invalid CVAT XML export") from exc
    if root.tag != "annotations":
        raise AnnotationAdapterError("expected CVAT annotations root")
    if sum(1 for _ in root.iter()) > MAX_XML_ELEMENTS:
        raise AnnotationAdapterError("CVAT XML element count exceeds limit")
    images = root.findall("image")
    if len(images) > MAX_TASKS:
        raise AnnotationAdapterError("CVAT image count exceeds limit")
    rows: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    skipped = 0
    for image in images:
        name = image.get("name")
        asset_id = _identifier(image.get("id"), "CVAT image id")
        if not isinstance(name, str) or not name or len(name) > 1024 or asset_id in seen_images:
            raise AnnotationAdapterError("invalid or duplicate CVAT image")
        seen_images.add(asset_id)
        width = _number(image.get("width"), "image width")
        height = _number(image.get("height"), "image height")
        if width <= 0 or height <= 0:
            raise AnnotationAdapterError("CVAT image dimensions must be positive")
        for child_index, shape in enumerate(list(image)):
            if shape.tag not in {"box", "polygon"}:
                skipped += 1
                continue
            label = _label(shape.get("label"))
            annotation_id = f"cvat-{asset_id}-{child_index}"
            if shape.tag == "box":
                x1, y1, x2, y2 = (_number(shape.get(k), k) for k in ("xtl", "ytl", "xbr", "ybr"))
                geometry = [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height]
                rows.append({"annotation_id": annotation_id, "task_type": "bbox", "label": label, "asset_id": f"cvat-image-{asset_id}", "geometry": geometry})
            else:
                points = shape.get("points")
                if not isinstance(points, str) or len(points) > 16_384:
                    raise AnnotationAdapterError("invalid CVAT polygon points")
                geometry = []
                try:
                    for pair in points.split(";"):
                        x, y = pair.split(",")
                        geometry.extend((float(x) / width, float(y) / height))
                except (ValueError, ZeroDivisionError) as exc:
                    raise AnnotationAdapterError("invalid CVAT polygon coordinates") from exc
                rows.append({"annotation_id": annotation_id, "task_type": "polygon", "label": label, "asset_id": f"cvat-image-{asset_id}", "geometry": geometry})
            if len(rows) > MAX_RECORDS:
                raise AnnotationAdapterError("normalized annotation count exceeds limit")
    for track in root.findall("track"):
        skipped += sum(1 for child in list(track) if child.tag in {"box", "polygon", "mask", "points", "skeleton"})
    return _normalized_dataset(rows, source_format="cvat-xml", source_label=source_label, imported_at=imported_at, skipped=skipped)


def import_via_json_export(
    payload: bytes, *, source_label: str, imported_at: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> AnnotationDataset:
    """Import VIA v2 project JSON rectangle and polygon regions."""
    _limits(max_bytes)
    data = _load_json(payload, max_bytes=max_bytes)
    if not isinstance(data, dict) or not isinstance(data.get("_via_img_metadata"), dict):
        raise AnnotationAdapterError("expected VIA v2 _via_img_metadata object")
    metadata = data["_via_img_metadata"]
    if len(metadata) > MAX_TASKS:
        raise AnnotationAdapterError("VIA image count exceeds limit")
    rows: list[dict[str, Any]] = []
    skipped = 0
    seen_ids: set[str] = set()
    for image_key, image in metadata.items():
        if not isinstance(image, dict) or not isinstance(image.get("filename"), str):
            raise AnnotationAdapterError("invalid VIA image metadata")
        image_id = _identifier(image_key, "VIA image id")
        if image_id in seen_ids:
            raise AnnotationAdapterError("duplicate VIA image id")
        seen_ids.add(image_id)
        regions = image.get("regions", [])
        if not isinstance(regions, list) or len(regions) > MAX_RESULTS_PER_TASK:
            raise AnnotationAdapterError("invalid VIA region list")
        asset_id = f"via-image-{image_id}"
        for region_index, region in enumerate(regions):
            if not isinstance(region, dict) or not isinstance(region.get("shape_attributes"), dict) or not isinstance(region.get("region_attributes"), dict):
                raise AnnotationAdapterError("invalid VIA region")
            shape, attributes = region["shape_attributes"], region["region_attributes"]
            labels = [value for value in attributes.values() if isinstance(value, str) and value.strip()]
            if not labels:
                skipped += 1
                continue
            label = _label(labels[0])
            name = shape.get("name")
            annotation_id = f"via-{image_id}-{region_index}"
            if name == "rect":
                x, y, width, height = (_number(shape.get(k), k) for k in ("x", "y", "width", "height"))
                image_width, image_height = _number(image.get("width"), "image width"), _number(image.get("height"), "image height")
                if image_width <= 0 or image_height <= 0:
                    raise AnnotationAdapterError("VIA image dimensions must be positive")
                geometry = [x / image_width, y / image_height, width / image_width, height / image_height]
                rows.append({"annotation_id": annotation_id, "task_type": "bbox", "label": label, "asset_id": asset_id, "geometry": geometry})
            elif name == "polygon":
                xs, ys = shape.get("all_points_x"), shape.get("all_points_y")
                if not isinstance(xs, list) or not isinstance(ys, list) or len(xs) != len(ys) or not 3 <= len(xs) <= 1024:
                    raise AnnotationAdapterError("invalid VIA polygon points")
                image_width, image_height = _number(image.get("width"), "image width"), _number(image.get("height"), "image height")
                if image_width <= 0 or image_height <= 0:
                    raise AnnotationAdapterError("VIA image dimensions must be positive")
                geometry = []
                for x, y in zip(xs, ys):
                    geometry.extend((_number(x, "polygon x") / image_width, _number(y, "polygon y") / image_height))
                rows.append({"annotation_id": annotation_id, "task_type": "polygon", "label": label, "asset_id": asset_id, "geometry": geometry})
            else:
                skipped += 1
            if len(rows) > MAX_RECORDS:
                raise AnnotationAdapterError("normalized annotation count exceeds limit")
    return _normalized_dataset(rows, source_format="via-v2-json", source_label=source_label, imported_at=imported_at, skipped=skipped)
