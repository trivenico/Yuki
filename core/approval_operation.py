"""Typed, canonical operation contracts for exact approval binding.

This module deliberately contains no executor. Capability adapters construct an
ApprovalOperation from validated arguments; the execution boundary recomputes
the digest from the same canonical payload before dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping


_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_operation_payload(payload: Mapping[str, Any]) -> bytes:
    """Encode a bounded JSON object deterministically, rejecting ambiguous data."""
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("operation payload must be a non-empty mapping")
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("operation payload must contain finite JSON values") from error
    if len(encoded) > 256_000:
        raise ValueError("operation payload exceeds the 256 KB limit")
    # Round-trip prevents custom Mapping implementations and non-string keys
    # from producing a representation different from the one being hashed.
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("operation payload must encode as a JSON object")
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
