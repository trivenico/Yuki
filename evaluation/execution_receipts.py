"""Canonical, bounded evidence contracts for candidate sandbox attempts.

This module does not launch a sandbox or attest to its isolation. It hashes the
exact byte strings supplied by the caller and creates tamper-evident records;
without a separately protected signer/attestor, those records are not proof
of who executed the artifact or what a remote runtime actually loaded.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from typing import Any

from evaluation.evolution_candidates import EvolutionCandidate

_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_MAX_INPUT_MANIFEST_BYTES = 256 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_RECORD_BYTES = 32 * 1024


def _canonical(value: Any, *, limit: int = _MAX_RECORD_BYTES) -> bytes:
    try:
        encoded = json.dumps(
            value, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("execution evidence must contain finite JSON values") from exc
    if len(encoded) > limit:
        raise ValueError("execution evidence exceeds its size limit")
    return encoded


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: str, field: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value.strip()


def _digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class CandidateExecutionManifest:
    schema: str
    attempt_id: str
    owner_id: str
    candidate_id: str
    candidate_digest: str
    artifact_digest: str
    artifact_bytes: int
    benchmark_id: str
    benchmark_version: int
    benchmark_digest: str
    input_manifest_digest: str
    input_manifest_bytes: int
    sandbox_image: str
    runtime_identity: str
    runner_version: str
    created_at: str
    manifest_digest: str

    def payload(self) -> dict[str, Any]:
        fields = asdict(self)
        fields.pop("manifest_digest")
        return fields

    def verify(self) -> bool:
        try:
            if self.schema != "yuki.candidate-execution-manifest.v1":
                return False
            if type(self.artifact_bytes) is not int or not 0 <= self.artifact_bytes <= _MAX_ARTIFACT_BYTES:
                return False
            if type(self.input_manifest_bytes) is not int or not 0 <= self.input_manifest_bytes <= _MAX_INPUT_MANIFEST_BYTES:
                return False
            _digest(self.candidate_digest, "candidate_digest")
            _digest(self.artifact_digest, "artifact_digest")
            _digest(self.benchmark_digest, "benchmark_digest")
            _digest(self.input_manifest_digest, "input_manifest_digest")
            return hashlib.sha256(_canonical(self.payload())).hexdigest() == self.manifest_digest
        except (TypeError, ValueError):
            return False


def create_execution_manifest(
    *,
    candidate: EvolutionCandidate,
    artifact: bytes,
    benchmark_id: str,
    benchmark_version: int,
    benchmark_digest: str,
    input_manifest: bytes,
    sandbox_image: str,
    runtime_identity: str,
    runner_version: str,
    created_at: str | None = None,
    attempt_id: str | None = None,
) -> CandidateExecutionManifest:
    """Hash staged artifact and input-manifest bytes, then bind them to candidate metadata."""
    if not isinstance(candidate, EvolutionCandidate):
        raise TypeError("candidate must be an EvolutionCandidate")
    if not isinstance(artifact, bytes) or len(artifact) > _MAX_ARTIFACT_BYTES:
        raise ValueError("artifact must be bytes within the 8 MiB limit")
    if not isinstance(input_manifest, bytes) or len(input_manifest) > _MAX_INPUT_MANIFEST_BYTES:
        raise ValueError("input_manifest must be bytes within the 256 KiB limit")
    if type(benchmark_version) is not int or benchmark_version < 1:
        raise ValueError("benchmark_version must be a positive integer")
    _digest(benchmark_digest, "benchmark_digest")
    fields = {
        "schema": "yuki.candidate-execution-manifest.v1",
        "attempt_id": _text(attempt_id or secrets.token_urlsafe(18), "attempt_id", 128),
        "owner_id": _text(candidate.owner_id, "owner_id", 200),
        "candidate_id": _text(candidate.candidate_id, "candidate_id", 128),
        "candidate_digest": _digest(candidate.payload_digest, "candidate_digest"),
        "artifact_digest": _sha256(artifact),
        "artifact_bytes": len(artifact),
        "benchmark_id": _text(benchmark_id, "benchmark_id", 100),
        "benchmark_version": benchmark_version,
        "benchmark_digest": benchmark_digest,
        "input_manifest_digest": _sha256(input_manifest),
        "input_manifest_bytes": len(input_manifest),
        "sandbox_image": _text(sandbox_image, "sandbox_image", 256),
        "runtime_identity": _text(runtime_identity, "runtime_identity", 256),
        "runner_version": _text(runner_version, "runner_version", 100),
        "created_at": _timestamp(created_at or datetime.now(timezone.utc).isoformat(), "created_at"),
    }
    manifest_digest = _sha256(_canonical(fields))
    result = CandidateExecutionManifest(**fields, manifest_digest=manifest_digest)
    if not result.verify():
        raise ValueError("execution manifest failed self-verification")
    return result


@dataclass(frozen=True, slots=True)
class CandidateExecutionReceipt:
    schema: str
    attempt_id: str
    manifest_digest: str
    artifact_digest: str
    output_digest: str
    output_bytes: int
    exit_code: int
    timed_out: bool
    started_at: str
    completed_at: str
    receipt_digest: str

    def payload(self) -> dict[str, Any]:
        fields = asdict(self)
        fields.pop("receipt_digest")
        return fields

    def verify(self, manifest: CandidateExecutionManifest) -> bool:
        try:
            if not isinstance(manifest, CandidateExecutionManifest) or not manifest.verify():
                return False
            if self.schema != "yuki.candidate-execution-receipt.v1":
                return False
            if self.attempt_id != manifest.attempt_id:
                return False
            if self.manifest_digest != manifest.manifest_digest or self.artifact_digest != manifest.artifact_digest:
                return False
            if type(self.output_bytes) is not int or not 0 <= self.output_bytes <= _MAX_OUTPUT_BYTES:
                return False
            if type(self.exit_code) is not int or not 0 <= self.exit_code <= 255:
                return False
            if type(self.timed_out) is not bool:
                return False
            started = _timestamp(self.started_at, "started_at")
            completed = _timestamp(self.completed_at, "completed_at")
            if datetime.fromisoformat(completed) < datetime.fromisoformat(started):
                return False
            _digest(self.output_digest, "output_digest")
            return _sha256(_canonical(self.payload())) == self.receipt_digest
        except (TypeError, ValueError):
            return False


def create_execution_receipt(
    *,
    manifest: CandidateExecutionManifest,
    output: bytes,
    exit_code: int,
    timed_out: bool,
    started_at: str,
    completed_at: str,
) -> CandidateExecutionReceipt:
    """Create a bounded receipt from captured output bytes; raw output is never retained."""
    if not isinstance(manifest, CandidateExecutionManifest) or not manifest.verify():
        raise ValueError("manifest must pass integrity verification")
    if not isinstance(output, bytes) or len(output) > _MAX_OUTPUT_BYTES:
        raise ValueError("output must be bytes within the 1 MiB limit")
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ValueError("exit_code must be an integer between 0 and 255")
    if type(timed_out) is not bool:
        raise TypeError("timed_out must be a boolean")
    started = _timestamp(started_at, "started_at")
    completed = _timestamp(completed_at, "completed_at")
    if datetime.fromisoformat(completed) < datetime.fromisoformat(started):
        raise ValueError("completed_at must not precede started_at")
    fields = {
        "schema": "yuki.candidate-execution-receipt.v1",
        "attempt_id": manifest.attempt_id,
        "manifest_digest": manifest.manifest_digest,
        "artifact_digest": manifest.artifact_digest,
        "output_digest": _sha256(output),
        "output_bytes": len(output),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "started_at": started,
        "completed_at": completed,
    }
    receipt = CandidateExecutionReceipt(**fields, receipt_digest=_sha256(_canonical(fields)))
    if not receipt.verify(manifest):
        raise ValueError("execution receipt failed self-verification")
    return receipt
