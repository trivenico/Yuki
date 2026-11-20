"""Bounded, metadata-only importer for Nuclei JSONL reports.

This module never executes templates or sends network requests. It discards raw
request/response material and strips URL credentials, query strings, and fragments.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_MAX_REPORT_BYTES = 8 * 1024 * 1024
_MAX_FINDINGS = 5_000
_MAX_LINE_BYTES = 16 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})


class NucleiReportError(ValueError):
    """Raised when an imported Nuclei report is malformed or exceeds limits."""


@dataclass(frozen=True)
class NucleiFinding:
    template_id: str
    name: str
    severity: str
    protocol: str
    target: str
    matched_location: str | None
    matcher_name: str | None
    extracted_result_count: int
    reported_timestamp: str | None
    provenance: str = "imported_nuclei_report_unverified"
    verified: bool = False
    execution: str = "report_import_only"


@dataclass(frozen=True)
class NucleiReport:
    findings: tuple[NucleiFinding, ...]
    source_size: int
    skipped_non_finding_lines: int = 0


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NucleiReportError("duplicate JSON object key")
        result[key] = value
    return result


def _bounded_text(value, field, limit, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise NucleiReportError(f"invalid {field}")
    return value.strip()


def _safe_location(value):
    if value is None:
        return None
    raw = _bounded_text(value, "matched-at URL", 4096)
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if not host or len(host) > 253:
            return None
        if any(char in host for char in "\r\n\t"):
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc += f":{port}"
        path = parsed.path[:2048] or "/"
        return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))
    except (ValueError, UnicodeError):
        return None


def _finding(record):
    if not isinstance(record, dict):
        raise NucleiReportError("each report line must be a JSON object")
    template_id = _bounded_text(record.get("template-id"), "template-id", 128, required=True)
    if not _ID_RE.fullmatch(template_id):
        raise NucleiReportError("invalid template-id")
    info = record.get("info")
    if not isinstance(info, dict):
        raise NucleiReportError("finding info must be an object")
    name = _bounded_text(info.get("name"), "finding name", 300, required=True)
    severity = _bounded_text(info.get("severity", "unknown"), "severity", 32).lower()
    if severity not in _SEVERITIES:
        severity = "unknown"
    protocol = _bounded_text(record.get("type", "unknown"), "protocol", 32).lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", protocol):
        protocol = "unknown"
    target = _bounded_text(record.get("host"), "target", 512, required=True)
    # Never preserve URL credentials, query parameters, fragments, or raw payloads.
    safe_location = _safe_location(record.get("matched-at"))
    matcher = _bounded_text(record.get("matcher-name"), "matcher-name", 128)
    extracted = record.get("extracted-results", [])
    if extracted is None:
        extracted = []
    if not isinstance(extracted, list) or len(extracted) > 100:
        raise NucleiReportError("extracted-results must be a bounded list")
    timestamp = _bounded_text(record.get("timestamp"), "timestamp", 80)
    return NucleiFinding(
        template_id=template_id,
        name=name,
        severity=severity,
        protocol=protocol,
        target=target,
        matched_location=safe_location,
        matcher_name=matcher,
        extracted_result_count=len(extracted),
        reported_timestamp=timestamp,
    )


def parse_nuclei_jsonl(payload: bytes, *, max_report_bytes: int = _MAX_REPORT_BYTES,
                       max_findings: int = _MAX_FINDINGS) -> NucleiReport:
    """Import bounded JSONL metadata only; does not run Nuclei or verify findings."""
    if not isinstance(payload, bytes):
        raise NucleiReportError("report input must be immutable bytes")
    if (type(max_report_bytes) is not int or not 1 <= max_report_bytes <= _MAX_REPORT_BYTES
            or type(max_findings) is not int or not 1 <= max_findings <= _MAX_FINDINGS):
        raise NucleiReportError("report limits are outside supported bounds")
    if len(payload) > max_report_bytes:
        raise NucleiReportError("report exceeds the configured byte limit")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NucleiReportError("report must be UTF-8") from exc
    findings = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
            raise NucleiReportError("report line exceeds the configured byte limit")
        try:
            record = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise NucleiReportError("report contains malformed JSON") from exc
        if not isinstance(record, dict) or "template-id" not in record:
            skipped += 1
            continue
        if len(findings) >= max_findings:
            raise NucleiReportError("report finding count exceeds the configured limit")
        findings.append(_finding(record))
    return NucleiReport(tuple(findings), len(payload), skipped)
