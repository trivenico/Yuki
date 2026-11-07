"""Evidence contracts for authorized, evidence-first reverse engineering.

This module only validates and authenticates structured observations. It does not
open targets, execute binaries, capture traffic, decompile code, or grant access.
All target acquisition and analysis must be performed by separately authorized,
bounded adapters.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 24
MAX_ITEMS = 20_000
MAX_STRING_CHARS = 256_000
_ALLOWED_OPERATIONS = frozenset({
    "inspect_artifact",
    "inspect_application",
    "inspect_binary",
    "inspect_website",
    "trace_behavior",
    "inspect_runtime",
    "inspect_network_capture",
    "inspect_model_artifact",
})
_ALLOWED_COVERAGE = frozenset({"complete", "partial", "unknown"})


class EvidenceError(ValueError):
    """Raised when an evidence record is malformed or fails integrity checks."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise EvidenceError("Evidence must contain finite, UTF-8 JSON values.") from exc


def _validate_json_tree(value: Any) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        current, depth = pending.pop()
        count += 1
        if count > MAX_ITEMS:
            raise EvidenceError("Evidence contains too many JSON values.")
        if depth > MAX_DEPTH:
            raise EvidenceError("Evidence exceeds the maximum nesting depth.")
        if current is None or isinstance(current, (bool, int)):
            continue
        if isinstance(current, float):
            if current != current or current in (float("inf"), float("-inf")):
                raise EvidenceError("Evidence numbers must be finite.")
            continue
        if isinstance(current, str):
            if len(current) > MAX_STRING_CHARS:
                raise EvidenceError("Evidence string exceeds the size limit.")
            continue
        if isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str) or len(key) > 512:
                    raise EvidenceError("Evidence object keys must be bounded strings.")
                pending.append((item, depth + 1))
            continue
        raise EvidenceError("Evidence contains a non-JSON value.")


def _required_text(value: Any, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EvidenceError(f"{field} must be a non-empty string of at most {limit} characters.")
    return value


def create_evidence_record(
    *,
    operation: str,
    target: Mapping[str, Any],
    provider: Mapping[str, Any],
    observed_at: str,
    normalized_result: Any,
    limitations: list[str] | tuple[str, ...] = (),
    coverage: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a content-addressed record from already-collected observations."""
    if operation not in _ALLOWED_OPERATIONS:
        raise EvidenceError("Unsupported evidence operation.")
    if not isinstance(target, Mapping) or not isinstance(provider, Mapping):
        raise EvidenceError("target and provider must be objects.")
    target_record = {
        "kind": _required_text(target.get("kind"), "target.kind", limit=64),
        "label": _required_text(target.get("label"), "target.label", limit=512),
        "sha256": _required_text(target.get("sha256"), "target.sha256", limit=64).lower(),
    }
    if len(target_record["sha256"]) != 64 or any(
        char not in "0123456789abcdef" for char in target_record["sha256"]
    ):
        raise EvidenceError("target.sha256 must be a hexadecimal SHA-256 digest.")
    provider_record = {
        "name": _required_text(provider.get("name"), "provider.name", limit=128),
        "version": _required_text(provider.get("version"), "provider.version", limit=128),
    }
    if not isinstance(limitations, (list, tuple)) or len(limitations) > 256:
        raise EvidenceError("limitations must be a bounded list.")
    normalized_limitations = [
        _required_text(item, "limitation", limit=2_000) for item in limitations
    ]
    coverage_value = dict(coverage or {})
    if set(coverage_value) - {"status", "scope"}:
        raise EvidenceError("coverage contains unsupported fields.")
    coverage_record = {
        "status": coverage_value.get("status", "unknown"),
        "scope": _required_text(coverage_value.get("scope", "unspecified"), "coverage.scope", limit=128),
    }
    if coverage_record["status"] not in _ALLOWED_COVERAGE:
        raise EvidenceError("coverage.status must be complete, partial, or unknown.")
    provenance_value = dict(provenance or {})
    if set(provenance_value) - {"adapter", "adapter_version", "source_ref"}:
        raise EvidenceError("provenance contains unsupported fields.")
    provenance_record = {
        "adapter": _required_text(provenance_value.get("adapter", "unspecified"), "provenance.adapter", limit=128),
        "adapter_version": _required_text(provenance_value.get("adapter_version", "unspecified"), "provenance.adapter_version", limit=128),
        "source_ref": provenance_value.get("source_ref"),
    }
    if provenance_record["source_ref"] is not None:
        _required_text(provenance_record["source_ref"], "provenance.source_ref", limit=2_048)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "target": target_record,
        "provider": provider_record,
        "observed_at": _required_text(observed_at, "observed_at", limit=64),
        "normalized_result": normalized_result,
        "limitations": normalized_limitations,
        "coverage": coverage_record,
        "provenance": provenance_record,
    }
    _validate_json_tree(payload)
    encoded = _canonical_json(payload)
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("Evidence record exceeds the 2 MiB limit.")
    return {"evidence_id": hashlib.sha256(encoded).hexdigest(), **payload}


def verify_evidence_record(record: Mapping[str, Any]) -> bool:
    """Verify exact schema, bounds and content identity; never trust an ID alone."""
    if not isinstance(record, Mapping):
        raise EvidenceError("Evidence record must be an object.")
    expected = {
        "evidence_id", "schema_version", "operation", "target", "provider",
        "observed_at", "normalized_result", "limitations", "coverage", "provenance",
    }
    if set(record) != expected:
        raise EvidenceError("Evidence record has missing or unsupported fields.")
    evidence_id = _required_text(record.get("evidence_id"), "evidence_id", limit=64)
    if len(evidence_id) != 64 or any(c not in "0123456789abcdef" for c in evidence_id):
        raise EvidenceError("evidence_id must be a hexadecimal SHA-256 digest.")
    payload = {key: record[key] for key in expected if key != "evidence_id"}
    _validate_json_tree(payload)
    encoded = _canonical_json(payload)
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("Evidence record exceeds the 2 MiB limit.")
    actual = hashlib.sha256(encoded).hexdigest()
    if actual != evidence_id:
        raise EvidenceError("Evidence integrity check failed.")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise EvidenceError("Unsupported evidence schema version.")
    # Revalidate using the producer path so imported records obey identical rules.
    rebuilt = create_evidence_record(
        operation=payload["operation"], target=payload["target"],
        provider=payload["provider"], observed_at=payload["observed_at"],
        normalized_result=payload["normalized_result"],
        limitations=payload["limitations"], coverage=payload["coverage"],
        provenance=payload["provenance"],
    )
    if rebuilt["evidence_id"] != evidence_id:
        raise EvidenceError("Evidence record does not match its canonical schema.")
    return True


def evidence_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact view only after the full record passes integrity checks."""
    verify_evidence_record(record)
    result = record["normalized_result"]
    return {
        "evidence_id": record["evidence_id"],
        "operation": record["operation"],
        "target": dict(record["target"]),
        "provider": dict(record["provider"]),
        "coverage": dict(record["coverage"]),
        "limitation_count": len(record["limitations"]),
        "result_type": type(result).__name__,
        "observed_at": record["observed_at"],
    }
