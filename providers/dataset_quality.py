"""Deterministic, privacy-conscious dataset quality assessment.

This module only inspects already-parsed records. It does not upload data,
modify source datasets, or make legal/consent decisions. PII checks are
heuristics and must not be treated as a guarantee that data is anonymous.
"""
from __future__ import annotations

import hashlib
import json
import re

from providers.dataset_records import DatasetRecordError, deduplicate_dataset_records

_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}(?![A-Za-z0-9.-])")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{7,}\d)(?!\w)")
_SECRET = re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}")


def assess_dataset_quality(records, *, max_prompt_chars=100_000,
                           max_response_chars=100_000, max_pii_findings=0):
    """Return a content-addressed quality report without returning source text."""
    if not isinstance(records, list) or not records:
        raise DatasetRecordError("nonempty_records_required")
    for name, value in (("max_prompt_chars", max_prompt_chars),
                        ("max_response_chars", max_response_chars),
                        ("max_pii_findings", max_pii_findings)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DatasetRecordError("invalid_" + name)
    if max_prompt_chars < 1 or max_response_chars < 1:
        raise DatasetRecordError("invalid_text_length_limit")

    dedup = deduplicate_dataset_records(records)
    findings = []
    total_prompt_chars = 0
    total_response_chars = 0
    pii_count = 0
    oversized = 0
    for index, record in enumerate(dedup["records"]):
        prompt = record["prompt"]
        response = record["response"]
        total_prompt_chars += len(prompt)
        total_response_chars += len(response)
        if not prompt.strip() or not response.strip():
            findings.append({"record_index": index, "code": "empty_training_text", "severity": "block"})
        if len(prompt) > max_prompt_chars or len(response) > max_response_chars:
            oversized += 1
            findings.append({"record_index": index, "code": "text_length_limit_exceeded", "severity": "block"})
        text = "\n".join((record.get("system", ""), prompt, response))
        matches = []
        if _EMAIL.search(text):
            matches.append("email_like_value")
        if _PHONE.search(text):
            matches.append("phone_like_value")
        if _SECRET.search(text):
            matches.append("credential_like_value")
        if matches:
            pii_count += len(matches)
            findings.append({
                "record_index": index,
                "code": "potential_sensitive_data",
                "categories": sorted(set(matches)),
                "severity": "block",
            })

    duplicate_count = dedup["duplicate_count"]
    if duplicate_count:
        findings.append({
            "code": "duplicate_records",
            "count": duplicate_count,
            "severity": "warning",
        })
    canonical = json.dumps(
        [{"content_sha256": item["content_sha256"]} for item in dedup["records"]],
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    blocking = sum(1 for item in findings if item["severity"] == "block")
    status = "blocked" if blocking or pii_count > max_pii_findings else "review_required" if findings else "passed"
    return {
        "schema_version": 1,
        "record_count": len(records),
        "unique_record_count": dedup["unique_count"],
        "duplicate_record_count": duplicate_count,
        "oversized_record_count": oversized,
        "potential_sensitive_finding_count": pii_count,
        "average_prompt_chars": round(total_prompt_chars / dedup["unique_count"], 2),
        "average_response_chars": round(total_response_chars / dedup["unique_count"], 2),
        "content_sha256": digest,
        "status": status,
        "findings": findings,
        "source_modified": False,
        "legal_rights_verified": False,
        "pii_scan_is_heuristic": True,
    }
