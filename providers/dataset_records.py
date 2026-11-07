"""Deterministic, bounded conversion of JSONL/CSV examples for Yuki.

This module parses plain files only. It never executes dataset code, accesses
network resources, or decides whether a dataset's rights are valid.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
from pathlib import Path


class DatasetRecordError(ValueError):
    """Dataset records are malformed, oversized, or unsupported."""


_DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_RECORD_BYTES = 1024 * 1024
_DEFAULT_MAX_RECORDS = 2_000_000
_ALLOWED_FIELDS = {"prompt", "response", "system", "source_id", "metadata"}


def _read_regular_file(path, max_file_bytes):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DatasetRecordError("dataset_file_unreadable_or_unsafe") from exc
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise DatasetRecordError("dataset_file_must_be_regular")
        if before.st_size > max_file_bytes:
            raise DatasetRecordError("dataset_file_size_limit_exceeded")
        data = stream.read(max_file_bytes + 1)
        after = os.fstat(stream.fileno())
        if len(data) > max_file_bytes:
            raise DatasetRecordError("dataset_file_size_limit_exceeded")
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise DatasetRecordError("dataset_file_changed_during_read")
        return data


def _canonical_record(value, index):
    if not isinstance(value, dict) or set(value) - _ALLOWED_FIELDS:
        raise DatasetRecordError("invalid_record_fields")
    prompt, response = value.get("prompt"), value.get("response")
    if not isinstance(prompt, str) or not prompt.strip():
        raise DatasetRecordError("record_prompt_required")
    if not isinstance(response, str) or not response.strip():
        raise DatasetRecordError("record_response_required")
    system = value.get("system", "")
    source_id = value.get("source_id", "")
    metadata = value.get("metadata", {})
    if not isinstance(system, str) or not isinstance(source_id, str):
        raise DatasetRecordError("invalid_record_metadata")
    if not isinstance(metadata, dict):
        raise DatasetRecordError("invalid_record_metadata")
    try:
        metadata_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DatasetRecordError("invalid_record_metadata") from exc
    normalize_newlines = lambda value: value.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))
    return {
        "schema_version": 1,
        "prompt": normalize_newlines(prompt.strip()),
        "response": normalize_newlines(response.strip()),
        "system": normalize_newlines(system.strip()),
        "source_id": source_id.strip(),
        "metadata": json.loads(metadata_json),
    }


def parse_dataset_records(
    path, *, format=None, max_file_bytes=_DEFAULT_MAX_FILE_BYTES,
    max_record_bytes=_DEFAULT_MAX_RECORD_BYTES, max_records=_DEFAULT_MAX_RECORDS,
):
    """Parse bounded JSONL or CSV and return canonical records and content digest."""
    for name, value in (("max_file_bytes", max_file_bytes),
                        ("max_record_bytes", max_record_bytes),
                        ("max_records", max_records)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise DatasetRecordError("invalid_" + name)
    raw = _read_regular_file(path, max_file_bytes)
    suffix = Path(path).suffix.lower()
    chosen = (format or suffix.lstrip(".")).lower()
    if chosen not in {"jsonl", "csv"}:
        raise DatasetRecordError("unsupported_dataset_format")
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise DatasetRecordError("dataset_must_be_utf8") from exc

    records = []
    if chosen == "jsonl":
        for index, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            if len(line.encode("utf-8")) > max_record_bytes:
                raise DatasetRecordError("record_size_limit_exceeded")
            try:
                item = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (json.JSONDecodeError, ValueError) as exc:
                raise DatasetRecordError("invalid_jsonl_record") from exc
            records.append(_canonical_record(item, index))
            if len(records) > max_records:
                raise DatasetRecordError("record_count_limit_exceeded")
    else:
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""))
            if reader.fieldnames is None or not {"prompt", "response"}.issubset(reader.fieldnames):
                raise DatasetRecordError("csv_prompt_response_columns_required")
            if set(reader.fieldnames) - _ALLOWED_FIELDS:
                raise DatasetRecordError("invalid_record_fields")
            for index, item in enumerate(reader, 1):
                if None in item:
                    raise DatasetRecordError("invalid_csv_row_width")
                row_size = sum(
                    len(value.encode("utf-8")) for value in item.values()
                    if isinstance(value, str)
                )
                if row_size > max_record_bytes:
                    raise DatasetRecordError("record_size_limit_exceeded")
                records.append(_canonical_record(item, index))
                if len(records) > max_records:
                    raise DatasetRecordError("record_count_limit_exceeded")
        except csv.Error as exc:
            raise DatasetRecordError("invalid_csv_dataset") from exc

    canonical = "".join(
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    return {
        "format": chosen,
        "record_count": len(records),
        "records": records,
        "canonical_jsonl": canonical,
        "canonical_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def deduplicate_dataset_records(records):
    """Keep first occurrence in input order using stable prompt/response identity."""
    if not isinstance(records, list):
        raise DatasetRecordError("records_must_be_list")
    seen, unique, duplicates = set(), [], []
    for index, record in enumerate(records):
        canonical = _canonical_record(record, index)
        identity = hashlib.sha256(
            json.dumps(
                [canonical["system"], canonical["prompt"], canonical["response"]],
                ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if identity in seen:
            duplicates.append({"index": index, "content_sha256": identity})
            continue
        seen.add(identity)
        unique.append({**canonical, "content_sha256": identity})
    return {"records": unique, "unique_count": len(unique),
            "duplicate_count": len(duplicates), "duplicates": duplicates}


def deterministic_dataset_split(records, *, train=0.9, validation=0.05, test=0.05, seed="yuki-v1"):
    """Assign records to stable splits using SHA-256, independent of input ordering."""
    if not isinstance(records, list) or not records:
        raise DatasetRecordError("nonempty_records_required")
    values = (train, validation, test)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in values):
        raise DatasetRecordError("invalid_split_ratios")
    if abs(sum(values) - 1.0) > 1e-9 or not train or not validation or not test:
        raise DatasetRecordError("split_ratios_must_sum_to_one_and_be_nonzero")
    if not isinstance(seed, str) or not seed or len(seed) > 128:
        raise DatasetRecordError("invalid_split_seed")

    buckets = {"train": [], "validation": [], "test": []}
    boundaries = (train, train + validation)
    for record in records:
        identity = record.get("content_sha256")
        if not isinstance(identity, str) or len(identity) != 64:
            raise DatasetRecordError("record_content_digest_required")
        score = int.from_bytes(hashlib.sha256((seed + ":" + identity).encode()).digest()[:8], "big") / 2**64
        split = "train" if score < boundaries[0] else (
            "validation" if score < boundaries[1] else "test"
        )
        buckets[split].append(identity)
    for values_list in buckets.values():
        values_list.sort()
    payload = {"seed": seed, "ratios": {"train": train, "validation": validation, "test": test},
               "splits": buckets}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {**payload, "split_manifest_sha256": hashlib.sha256(encoded.encode()).hexdigest()}
