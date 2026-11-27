"""Verification contract for independently signed runner execution attestations.

This module verifies Ed25519 claims; it does not create signatures or launch a runner.
The private key must remain inside a separately protected runner boundary.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json, re, sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from core.sqlite_file_security import prepare_private_sqlite_path
from evaluation.execution_receipts import CandidateExecutionManifest

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")

def _time(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("timestamp must be bounded ISO-8601 text")
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)

def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")

@dataclass(frozen=True, slots=True)
class RunnerVerificationKey:
    owner_id: str
    key_id: str
    public_key: bytes
    valid_from: str
    revoked_at: str | None = None

@dataclass(frozen=True, slots=True)
class RunnerAttestation:
    schema: str
    owner_id: str
    runner_id: str
    key_id: str
    nonce: str
    issued_at: str
    manifest_digest: str
    loaded_artifact_digest: str
    exit_code: int
    timed_out: bool
    output_digest: str
    signature: bytes
    def payload(self) -> dict[str, Any]:
        value = asdict(self); value.pop("signature"); return value

@dataclass(frozen=True, slots=True)
class VerifiedRunnerAttestation:
    owner_id: str
    runner_id: str
    key_id: str
    nonce: str
    manifest_digest: str
    loaded_artifact_digest: str
    issued_at: str
    output_digest: str

class RunnerAttestationReplayStore:
    """Atomic persistent nonce consumption with bounded retention."""
    def __init__(self, database_path: str | Path, *, retention_seconds: int = 86400):
        if type(retention_seconds) is not int or not 60 <= retention_seconds <= 604800:
            raise ValueError("retention_seconds must be between 60 and 604800")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.retention_seconds = retention_seconds
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS runner_attestation_nonces (
                owner_id TEXT NOT NULL, key_id TEXT NOT NULL, nonce TEXT NOT NULL,
                issued_at TEXT NOT NULL, PRIMARY KEY(owner_id,key_id,nonce))""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS runner_attestation_nonces_no_update
                BEFORE UPDATE ON runner_attestation_nonces
                BEGIN SELECT RAISE(ABORT, 'attestation nonce records are immutable'); END""")
    def _connect(self):
        self.database_path = prepare_private_sqlite_path(self.database_path)
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000"); db.execute("PRAGMA synchronous=FULL")
        return db
    def consume(self, *, owner_id: str, key_id: str, nonce: str,
                issued_at: datetime, now: datetime) -> None:
        cutoff = (now - timedelta(seconds=self.retention_seconds)).isoformat()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM runner_attestation_nonces WHERE issued_at < ?", (cutoff,))
            try:
                db.execute("INSERT INTO runner_attestation_nonces VALUES(?,?,?,?)",
                           (owner_id,key_id,nonce,issued_at.isoformat()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("runner attestation nonce has already been used") from exc

def verify_runner_attestation(*, attestation: RunnerAttestation,
    manifest: CandidateExecutionManifest, key: RunnerVerificationKey,
    replay_store: RunnerAttestationReplayStore, now: datetime | None = None,
    max_age_seconds: int = 300, allowed_clock_skew_seconds: int = 30
) -> VerifiedRunnerAttestation:
    """Verify pinned-key signature, artifact binding, freshness and replay."""
    if not isinstance(attestation, RunnerAttestation) or not isinstance(key, RunnerVerificationKey):
        raise TypeError("attestation and key must use their declared contracts")
    if not isinstance(manifest, CandidateExecutionManifest) or not manifest.verify():
        raise ValueError("execution manifest is invalid")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise ValueError("max_age_seconds must be between 1 and 3600")
    if type(allowed_clock_skew_seconds) is not int or not 0 <= allowed_clock_skew_seconds <= 300:
        raise ValueError("allowed_clock_skew_seconds must be between 0 and 300")
    if attestation.schema != "yuki.runner-attestation.v1":
        raise ValueError("unsupported runner attestation schema")
    for value, field, limit in ((attestation.owner_id,"owner_id",200),
        (attestation.runner_id,"runner_id",128),(attestation.key_id,"key_id",128)):
        if not isinstance(value,str) or not value.strip() or len(value)>limit or "\x00" in value:
            raise ValueError(f"{field} is invalid")
    if attestation.owner_id != manifest.owner_id or key.owner_id != manifest.owner_id:
        raise ValueError("runner attestation owner mismatch")
    if attestation.key_id != key.key_id: raise ValueError("runner attestation key ID mismatch")
    if not isinstance(key.public_key,bytes) or len(key.public_key)!=32:
        raise ValueError("Ed25519 public key must be exactly 32 bytes")
    if not isinstance(attestation.nonce,str) or not _NONCE.fullmatch(attestation.nonce):
        raise ValueError("nonce must be a URL-safe random value of at least 22 characters")
    if attestation.manifest_digest != manifest.manifest_digest or attestation.loaded_artifact_digest != manifest.artifact_digest:
        raise ValueError("runner did not attest the manifest's exact artifact digest")
    if any(not isinstance(v,str) or not _DIGEST.fullmatch(v) for v in
           (attestation.manifest_digest,attestation.loaded_artifact_digest,attestation.output_digest)):
        raise ValueError("attestation digest is invalid")
    if type(attestation.exit_code) is not int or not 0 <= attestation.exit_code <= 255:
        raise ValueError("exit_code is invalid")
    if type(attestation.timed_out) is not bool: raise ValueError("timed_out must be boolean")
    issued=_time(attestation.issued_at); valid_from=_time(key.valid_from)
    if issued < valid_from: raise ValueError("runner key was not yet valid at attestation time")
    if key.revoked_at is not None and issued >= _time(key.revoked_at):
        raise ValueError("runner signing key is revoked")
    current=now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("verification clock must be timezone-aware")
    current=current.astimezone(timezone.utc)
    if issued > current+timedelta(seconds=allowed_clock_skew_seconds):
        raise ValueError("runner attestation timestamp is in the future")
    if current-issued > timedelta(seconds=max_age_seconds):
        raise ValueError("runner attestation is stale")
    if not isinstance(attestation.signature,bytes) or len(attestation.signature)!=64:
        raise ValueError("Ed25519 signature must be exactly 64 bytes")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError("runner attestation verification requires the cryptography extra") from exc
    try:
        Ed25519PublicKey.from_public_bytes(key.public_key).verify(
            attestation.signature,_canonical(attestation.payload()))
    except Exception as exc: raise ValueError("runner attestation signature is invalid") from exc
    replay_store.consume(owner_id=attestation.owner_id,key_id=attestation.key_id,
                         nonce=attestation.nonce,issued_at=issued,now=current)
    return VerifiedRunnerAttestation(attestation.owner_id,attestation.runner_id,
        attestation.key_id,attestation.nonce,attestation.manifest_digest,
        attestation.loaded_artifact_digest,issued.isoformat(),attestation.output_digest)
