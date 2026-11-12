"""Typed, canonical operation contracts for exact approval binding.

Capability adapters construct an ApprovalOperation from validated arguments.
The execution boundary must verify the same normalized payload immediately
before dispatch; this module intentionally contains no executor.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping


_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PAYLOAD_BYTES = 256_000
_MAX_DEPTH = 32


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    """Accept only unambiguous JSON primitives, lists and string-keyed dicts."""
    if depth > _MAX_DEPTH:
        raise ValueError("operation payload exceeds the maximum nesting depth")
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        import math
        if not math.isfinite(value):
            raise ValueError("operation payload numbers must be finite")
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("operation payload object keys must be strings")
            _validate_json_value(item, depth=depth + 1)
        return
    raise ValueError("operation payload contains a non-JSON value")


def canonical_operation_payload(payload: Mapping[str, Any]) -> bytes:
    """Encode a bounded JSON object deterministically, rejecting ambiguous data."""
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("operation payload must be a non-empty mapping")
    # Copy only a plain dict after validating all nested values. This prevents
    # custom encoders, tuple coercion, non-string key coercion and object hooks
    # from changing the operation represented by the approval digest.
    normalized = dict(payload)
    if any(type(key) is not str for key in normalized):
        raise ValueError("operation payload object keys must be strings")
    _validate_json_value(normalized)
    try:
        encoded = json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("operation payload must contain valid finite JSON values") from error
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise ValueError("operation payload exceeds the 256 KB limit")
    return encoded


@dataclass(frozen=True)
class ApprovalOperation:
    """Immutable typed operation envelope bound to its exact canonical payload."""

    schema_version: int
    operation_type: str
    capability: str
    target: str
    payload_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported approval operation schema version")
        if not isinstance(self.operation_type, str) or not _OPERATION_ID.fullmatch(self.operation_type):
            raise ValueError("operation_type must be a bounded stable identifier")
        for field in ("capability", "target"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 256 or value != value.strip():
                raise ValueError(f"{field} must be non-empty bounded text")
        if not isinstance(self.payload_sha256, str) or not _SHA256.fullmatch(self.payload_sha256):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 digest")

    @classmethod
    def from_payload(
        cls, *, operation_type: str, capability: str, target: str,
        payload: Mapping[str, Any],
    ) -> "ApprovalOperation":
        encoded = canonical_operation_payload(payload)
        return cls(
            schema_version=1,
            operation_type=operation_type,
            capability=capability,
            target=target,
            payload_sha256=hashlib.sha256(encoded).hexdigest(),
        )

    def verify_payload(self, payload: Mapping[str, Any]) -> bool:
        encoded = canonical_operation_payload(payload)
        return hashlib.sha256(encoded).hexdigest() == self.payload_sha256

    def verify_policy_request(self, request: Any) -> bool:
        """Require exact capability, target and digest agreement with policy."""
        return (
            getattr(request, "capability", None) == self.capability
            and getattr(request, "target", None) == self.target
            and getattr(request, "argument_sha256", None) == self.payload_sha256
        )
