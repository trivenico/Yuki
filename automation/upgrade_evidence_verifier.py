"""Ed25519 verifier for isolated-upgrade runner evidence.

Trust is explicit: callers must provision trusted public keys and allowed
issuer/audience/runner identities. This module verifies evidence authenticity
and freshness; it does not create isolation or make an untrusted runner safe.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Mapping, AbstractSet

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from automation.upgrade_execution_contracts import ExecutionEvidence


class EvidenceVerificationError(RuntimeError):
    """Evidence failed a trust, signature, freshness, or replay check."""


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise EvidenceVerificationError("evidence_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise EvidenceVerificationError("evidence_timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


class Ed25519EvidenceVerifier:
    """Verify signed claims and persist nonce consumption to prevent replay."""

    def __init__(
        self, database_path: str | Path, *,
        trusted_public_keys: Mapping[str, bytes],
        allowed_issuers: AbstractSet[str],
        allowed_audiences: AbstractSet[str],
        allowed_runner_identities: AbstractSet[str],
        max_age_seconds: int = 900,
        clock_skew_seconds: int = 60,
    ) -> None:
        if not trusted_public_keys:
            raise ValueError("trusted_public_keys_required")
        if not allowed_issuers or not allowed_audiences or not allowed_runner_identities:
            raise ValueError("evidence_identity_allowlists_required")
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 86400:
            raise ValueError("max_age_seconds_invalid")
        if isinstance(clock_skew_seconds, bool) or not isinstance(clock_skew_seconds, int) or not 0 <= clock_skew_seconds <= 600:
            raise ValueError("clock_skew_seconds_invalid")
        self._keys: dict[str, Ed25519PublicKey] = {}
        for key_id, raw_key in trusted_public_keys.items():
            if not isinstance(key_id, str) or not key_id or not isinstance(raw_key, bytes) or len(raw_key) != 32:
                raise ValueError("trusted_public_key_invalid")
            self._keys[key_id] = Ed25519PublicKey.from_public_bytes(raw_key)
        self._issuers = frozenset(allowed_issuers)
        self._audiences = frozenset(allowed_audiences)
        self._runners = frozenset(allowed_runner_identities)
        self._max_age = timedelta(seconds=max_age_seconds)
        self._skew = timedelta(seconds=clock_skew_seconds)
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS consumed_evidence_nonces (
                    key_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    consumed_at TEXT NOT NULL,
                    PRIMARY KEY(key_id, nonce)
                )
            """)

    def verify(self, evidence: ExecutionEvidence, *, now: datetime | None = None) -> str:
        if not isinstance(evidence, ExecutionEvidence):
            raise TypeError("evidence_must_be_ExecutionEvidence")
        if evidence.key_id not in self._keys:
            raise EvidenceVerificationError("evidence_signing_key_untrusted")
        if evidence.issuer not in self._issuers:
            raise EvidenceVerificationError("evidence_issuer_untrusted")
        if evidence.audience not in self._audiences:
            raise EvidenceVerificationError("evidence_audience_mismatch")
        if evidence.runner_identity not in self._runners:
            raise EvidenceVerificationError("evidence_runner_identity_untrusted")

        started = _timestamp(evidence.started_at)
        finished = _timestamp(evidence.finished_at)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("verification_clock_must_be_timezone_aware")
        current = current.astimezone(timezone.utc)
        if finished < started or finished > current + self._skew:
            raise EvidenceVerificationError("evidence_time_order_invalid")
        if current - finished > self._max_age:
            raise EvidenceVerificationError("evidence_expired")
        if started > current + self._skew:
            raise EvidenceVerificationError("evidence_started_in_future")

        try:
            signature = base64.b64decode(evidence.signature, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EvidenceVerificationError("evidence_signature_encoding_invalid") from exc
        if len(signature) != 64:
            raise EvidenceVerificationError("evidence_signature_length_invalid")
        payload = _canonical_bytes(evidence.signed_payload())
        try:
            self._keys[evidence.key_id].verify(signature, payload)
        except InvalidSignature as exc:
            raise EvidenceVerificationError("evidence_signature_invalid") from exc

        payload_digest = hashlib.sha256(payload).hexdigest()
        with sqlite3.connect(self.database_path, timeout=10, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO consumed_evidence_nonces(key_id, nonce, payload_digest, consumed_at) VALUES (?, ?, ?, ?)",
                    (evidence.key_id, evidence.nonce, payload_digest, current.isoformat()),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise EvidenceVerificationError("evidence_nonce_replayed") from exc
            except Exception:
                db.execute("ROLLBACK")
                raise
        return payload_digest
