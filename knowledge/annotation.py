"""Vendor-neutral, bounded annotation interchange for supervised datasets."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 20_000
MAX_LINE_BYTES = 32 * 1024
MAX_LABEL_CHARS = 256
MAX_TEXT_CHARS = 16_384


class AnnotationImportError(ValueError):
    """Raised when annotation input is invalid or exceeds safety limits."""


@dataclass(frozen=True, slots=True)
class Annotation:
    annotation_id: str
    task_type: str
    label: str
    asset_id: str
    geometry: tuple[float, ...] = ()
    text_start: int | None = None
    text_end: int | None = None
    text_value: str | None = None
    asset_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class AnnotationEvidence:
    evidence_id: str
    source_format: str
    source_label: str
    imported_at: str
    content_sha256: str
    provenance: str = "owner_supplied_unverified"
    verified: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationDataset:
    annotations: tuple[Annotation, ...]
    evidence: tuple[AnnotationEvidence, ...]
    skipped_records: int = 0


def _text(value: Any, field: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise AnnotationImportError(f"invalid {field}")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnnotationImportError("geometry values must be numbers")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise AnnotationImportError("geometry must be finite and normalized to [0, 1]")
    return result


def _parse_record(record: Any) -> Annotation:
    if not isinstance(record, dict):
        raise AnnotationImportError("each record must be an object")
    allowed = {
        "annotation_id", "task_type", "label", "asset_id", "geometry",
        "text_start", "text_end", "text_value", "asset_sha256",
    }
    if set(record) - allowed or not {"annotation_id", "task_type", "label", "asset_id"} <= set(record):
        raise AnnotationImportError("unknown or missing annotation fields")
    annotation_id = _text(record["annotation_id"], "annotation_id", 128)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", annotation_id):
        raise AnnotationImportError("invalid annotation_id")
    task_type = _text(record["task_type"], "task_type", 32)
    if task_type not in {"classification", "bbox", "polygon", "text_span"}:
        raise AnnotationImportError("unsupported task_type")
    label = _text(record["label"], "label", MAX_LABEL_CHARS)
    asset_id = _text(record["asset_id"], "asset_id", 256)
    asset_sha256 = record.get("asset_sha256")
    if asset_sha256 is not None:
        asset_sha256 = _text(asset_sha256, "asset_sha256", 64)
        if not re.fullmatch(r"[A-Fa-f0-9]{64}", asset_sha256):
            raise AnnotationImportError("asset_sha256 must be a 64-character hexadecimal SHA-256 digest")
        asset_sha256 = asset_sha256.lower()
    geometry: tuple[float, ...] = ()
    start = end = None
    text_value = None
    if task_type == "classification":
        if any(key in record for key in ("geometry", "text_start", "text_end", "text_value")):
            raise AnnotationImportError("classification cannot contain geometry or text offsets")
    elif task_type == "bbox":
        raw = record.get("geometry")
        if not isinstance(raw, list) or len(raw) != 4:
            raise AnnotationImportError("bbox geometry must contain x, y, width, height")
        geometry = tuple(_number(value) for value in raw)
        if geometry[2] <= 0 or geometry[3] <= 0 or geometry[0] + geometry[2] > 1 or geometry[1] + geometry[3] > 1:
            raise AnnotationImportError("bbox must have positive size and remain inside the image")
        if any(key in record for key in ("text_start", "text_end", "text_value")):
            raise AnnotationImportError("bbox cannot contain text offsets")
    elif task_type == "polygon":
        raw = record.get("geometry")
        if not isinstance(raw, list) or len(raw) < 6 or len(raw) > 2048 or len(raw) % 2:
            raise AnnotationImportError("polygon requires 3 to 1024 x/y coordinate pairs")
        geometry = tuple(_number(value) for value in raw)
        if len(set(zip(geometry[::2], geometry[1::2]))) < 3:
            raise AnnotationImportError("polygon requires at least three distinct points")
        if any(key in record for key in ("text_start", "text_end", "text_value")):
            raise AnnotationImportError("polygon cannot contain text offsets")
    else:
        if "geometry" in record or not {"text_start", "text_end", "text_value"} <= set(record):
            raise AnnotationImportError("text_span requires text offsets and source text")
        text_value = _text(record["text_value"], "text_value", MAX_TEXT_CHARS, empty=True)
        start, end = record["text_start"], record["text_end"]
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise AnnotationImportError("text offsets must be integers")
        if not 0 <= start < end <= len(text_value):
            raise AnnotationImportError("text offsets are outside the supplied text")
    return Annotation(
        annotation_id=annotation_id, task_type=task_type, label=label, asset_id=asset_id,
        geometry=geometry, text_start=start, text_end=end, text_value=text_value,
        asset_sha256=asset_sha256,
    )


def import_annotation_jsonl(
    payload: bytes,
    *,
    source_format: str,
    source_label: str,
    imported_at: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> AnnotationDataset:
    """Import normalized JSONL; vendor exports need explicit adapters first."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_INPUT_BYTES:
        raise AnnotationImportError("invalid max_bytes")
    if not isinstance(payload, bytes) or len(payload) > max_bytes:
        raise AnnotationImportError("input must be bytes within the configured size limit")
    source_format = _text(source_format, "source_format", 64)
    source_label = _text(source_label, "source_label", 256)
    imported_at = _text(imported_at, "imported_at", 64)
    annotations = []
    ids = set()
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            continue
        if len(raw_line) > MAX_LINE_BYTES:
            raise AnnotationImportError("JSONL record exceeds line limit")
        if len(annotations) >= MAX_RECORDS:
            raise AnnotationImportError("too many annotations")
        try:
            record = json.loads(raw_line, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AnnotationImportError("invalid JSONL record") from exc
        item = _parse_record(record)
        if item.annotation_id in ids:
            raise AnnotationImportError("duplicate annotation_id")
        ids.add(item.annotation_id)
        annotations.append(item)
    digest = hashlib.sha256(payload).hexdigest()
    evidence = AnnotationEvidence(
        evidence_id=digest,
        source_format=source_format,
        source_label=source_label,
        imported_at=imported_at,
        content_sha256=digest,
    )
    return AnnotationDataset(tuple(annotations), (evidence,))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
