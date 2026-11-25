"""Verify signed authorization-decision receipts from an independent evidence source.

The receipt source and issuer-key resolver are host adapters. Production must
back them with a separate policy/identity trust domain; reading the Yuki audit
database as the receipt source defeats the purpose of this verifier.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol, runtime_checkable


_HEX = re.compile(r"^[0-9a-f]{64}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_RECEIPT_FIELDS = (
    "receipt_id", "issuer_key_id", "issued_at", "decision_hash",
    "principal_hash", "tenant_hash", "agent_hash", "action",
    "resource_hash", "task_hash", "delegation_hash", "approval_hash",
    "risk", "allowed", "policy_revision_hash", "reason_code",
)




@runtime_checkable
class AuthorizationDecisionEvidenceRecorder(Protocol):
    """Record a signed decision receipt in an independently administered source.

    Implementations must obtain the signature from the policy/identity issuer and
    durably retain the receipt outside the Yuki audit-writer database before
    returning its opaque receipt ID. They must not sign locally with a key shared
    with the audit writer.
    """

    def record_decision(self, request, decision) -> str: ...


@runtime_checkable
class AuthorizationDecisionReceiptSource(Protocol):
    """Fetch independently retained receipts by their decision digest."""

    def fetch_receipts(self, decision_hashes: tuple[str, ...]) -> tuple[dict, ...]: ...


@runtime_checkable
class AuthorizationDecisionIssuerKeyResolver(Protocol):
    """Resolve active, non-revoked Ed25519 public keys by issuer key ID."""

    def public_key_bytes(self, issuer_key_id: str) -> bytes: ...


@dataclass(frozen=True)
class SignedAuthorizationDecisionReceipt:
    """Canonical receipt payload plus detached Ed25519 signature."""

    receipt_id: str
    issuer_key_id: str
    issued_at: str
    decision_hash: str
    principal_hash: str
    tenant_hash: str | None
    agent_hash: str | None
    action: str
    resource_hash: str
    task_hash: str | None
    delegation_hash: str | None
    approval_hash: str | None
    risk: str
    allowed: int
    policy_revision_hash: str
    reason_code: str
    signature: str

    def payload(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "issuer_key_id": self.issuer_key_id,
            "issued_at": self.issued_at,
            "decision_hash": self.decision_hash,
            "principal_hash": self.principal_hash,
            "tenant_hash": self.tenant_hash,
            "agent_hash": self.agent_hash,
            "action": self.action,
            "resource_hash": self.resource_hash,
            "task_hash": self.task_hash,
            "delegation_hash": self.delegation_hash,
            "approval_hash": self.approval_hash,
            "risk": self.risk,
            "allowed": self.allowed,
            "policy_revision_hash": self.policy_revision_hash,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "SignedAuthorizationDecisionReceipt":
        if not isinstance(value, dict) or set(value) != set(_RECEIPT_FIELDS) | {"signature"}:
            raise ValueError("authorization_decision_receipt_fields_invalid")
        return cls(**value)

    def signing_bytes(self) -> bytes:
        return json.dumps(
            self.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")


class Ed25519AuthorizationAuditEvidenceVerifier:
    """Correlate every audit event with one independently sourced signed receipt.

    The key resolver must reject unknown, expired, or revoked issuer keys. The
    receipt source must be independently administered and retain issuer output
    before/independently of the audit writer's database transaction.
    """

    def __init__(
        self,
        receipt_source: AuthorizationDecisionReceiptSource,
        key_resolver: AuthorizationDecisionIssuerKeyResolver,
    ):
        if not isinstance(receipt_source, AuthorizationDecisionReceiptSource):
            raise TypeError("independent_authorization_decision_receipt_source_required")
        if not isinstance(key_resolver, AuthorizationDecisionIssuerKeyResolver):
            raise TypeError("authorization_decision_issuer_key_resolver_required")
        self._receipt_source = receipt_source
        self._key_resolver = key_resolver

    @staticmethod
    def _valid_text(value: object, maximum: int) -> bool:
        return isinstance(value, str) and bool(value) and len(value) <= maximum and value == value.strip()

    @staticmethod
    def _valid_digest(value: object, *, optional: bool = False) -> bool:
        return value is None if optional and value is None else isinstance(value, str) and _HEX.fullmatch(value) is not None

    @staticmethod
    def _decode_signature(value: object) -> bytes:
        if not isinstance(value, str) or len(value) != 86 or not _B64URL.fullmatch(value):
            raise ValueError("authorization_decision_receipt_signature_encoding_invalid")
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(decoded) != 64:
            raise ValueError("authorization_decision_receipt_signature_length_invalid")
        return decoded

    def _verify_receipt(self, receipt: SignedAuthorizationDecisionReceipt) -> None:
        if (
            not self._valid_text(receipt.receipt_id, 256)
            or not self._valid_text(receipt.issuer_key_id, 128)
            or not self._valid_text(receipt.action, 128)
            or not self._valid_text(receipt.reason_code, 128)
            or not self._valid_text(receipt.issued_at, 64)
            or not isinstance(receipt.risk, str)
            or receipt.risk not in {"low", "medium", "high", "critical"}
            or type(receipt.allowed) is not int
            or receipt.allowed not in (0, 1)
        ):
            raise ValueError("authorization_decision_receipt_metadata_invalid")
        for field in ("decision_hash", "principal_hash", "resource_hash", "policy_revision_hash"):
            if not self._valid_digest(getattr(receipt, field)):
                raise ValueError("authorization_decision_receipt_digest_invalid")
        for field in ("tenant_hash", "agent_hash", "task_hash", "delegation_hash", "approval_hash"):
            if not self._valid_digest(getattr(receipt, field), optional=True):
                raise ValueError("authorization_decision_receipt_optional_digest_invalid")
        try:
            timestamp = datetime.fromisoformat(receipt.issued_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("authorization_decision_receipt_timestamp_invalid") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("authorization_decision_receipt_timestamp_invalid")
        key_bytes = self._key_resolver.public_key_bytes(receipt.issuer_key_id)
        if not isinstance(key_bytes, bytes) or len(key_bytes) != 32:
            raise ValueError("authorization_decision_issuer_key_invalid")
        # Optional production-storage dependency; import only when this verifier is used.
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(key_bytes).verify(
                self._decode_signature(receipt.signature), receipt.signing_bytes(),
            )
        except ImportError as exc:
            raise RuntimeError("cryptography_dependency_required_for_decision_receipts") from exc
        except Exception as exc:
            raise ValueError("authorization_decision_receipt_signature_invalid") from exc

    def verify_batch(self, previous, current, events: tuple[dict, ...]) -> bool:
        if not isinstance(events, tuple) or not events:
            return False
        decision_hashes = tuple(event.get("decision_hash") for event in events)
        if (
            any(not isinstance(value, str) or _HEX.fullmatch(value) is None for value in decision_hashes)
            or len(set(decision_hashes)) != len(decision_hashes)
        ):
            return False
        receipts = self._receipt_source.fetch_receipts(decision_hashes)
        if not isinstance(receipts, tuple) or len(receipts) != len(events):
            return False
        by_decision = {}
        seen_receipt_ids = set()
        for raw in receipts:
            receipt = SignedAuthorizationDecisionReceipt.from_mapping(raw)
            if not isinstance(receipt.decision_hash, str) or _HEX.fullmatch(receipt.decision_hash) is None:
                return False
            if not isinstance(receipt.receipt_id, str) or receipt.receipt_id in seen_receipt_ids:
                return False
            if receipt.decision_hash in by_decision:
                return False
            self._verify_receipt(receipt)
            by_decision[receipt.decision_hash] = receipt
            seen_receipt_ids.add(receipt.receipt_id)
        if set(by_decision) != set(decision_hashes):
            return False
        for event in events:
            receipt = by_decision[event["decision_hash"]]
            for field in (
                "decision_hash", "principal_hash", "tenant_hash", "agent_hash",
                "action", "resource_hash", "task_hash", "delegation_hash",
                "approval_hash", "risk", "allowed", "policy_revision_hash",
                "reason_code",
            ):
                if receipt.payload()[field] != event.get(field):
                    return False
        return True
