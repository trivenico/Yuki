"""Strict, versioned contracts for governed isolated upgrade execution.

These schemas validate and fingerprint requests/results. They do not launch
containers, provide isolation, sign evidence, or verify cryptographic claims.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_CLAIM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,499}$")
_TEST_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_ALLOWED_RUNNER_KINDS = frozenset({"container", "vm", "github_actions"})
_ALLOWED_OUTCOMES = frozenset({"passed", "failed", "timed_out", "cancelled", "rejected"})


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field}_invalid")
    return value


def _claim(value: str, field: str) -> str:
    if not isinstance(value, str) or not _CLAIM.fullmatch(value):
        raise ValueError(f"{field}_invalid")
    return value


def _digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field}_invalid")
    return value


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _targets(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not values or len(values) > 64:
        raise ValueError("test_targets_invalid")
    result = tuple(values)
    if any(not isinstance(item, str) or not _TEST_MODULE.fullmatch(item) for item in result):
        raise ValueError("test_target_invalid")
    if len(set(result)) != len(result):
        raise ValueError("test_targets_duplicate")
    return tuple(sorted(result))


@dataclass(frozen=True)
class ExecutionRequest:
    """Immutable admission request bound to exact proposal and execution policy."""

    proposal_id: str
    snapshot_digest: str
    snapshot_object_digest: str
    snapshot_version: str
    test_targets: tuple[str, ...]
    test_plan_digest: str
    runner_kind: str
    runner_image_digest: str
    architecture: str
    runtime_version: str
    policy_digest: str
    execution_id: str
    idempotency_key: str
    requesting_principal: str
    reviewer: str
    approval_id: str
    schema_version: int = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != _SCHEMA_VERSION:
            raise ValueError("schema_version_unsupported")
        for field in ("proposal_id", "snapshot_version", "architecture",
                      "runtime_version", "execution_id", "idempotency_key",
                      "requesting_principal", "reviewer", "approval_id"):
            _identifier(getattr(self, field), field)
        for field in ("snapshot_digest", "snapshot_object_digest",
                      "runner_image_digest", "policy_digest", "test_plan_digest"):
            _digest(getattr(self, field), field)
        if self.runner_kind not in _ALLOWED_RUNNER_KINDS:
            raise ValueError("runner_kind_unsupported")
        if self.requesting_principal != self.reviewer:
            raise ValueError("reviewer_identity_mismatch")
        normalized = _targets(self.test_targets)
        if normalized != self.test_targets:
            raise ValueError("test_targets_must_be_canonical")
        expected = _canonical_digest({"schema_version": _SCHEMA_VERSION,
                                      "test_targets": list(normalized)})
        if self.test_plan_digest != expected:
            raise ValueError("test_plan_digest_mismatch")

    @classmethod
    def create(
        cls, *, proposal_id: str, snapshot_digest: str,
        snapshot_object_digest: str, snapshot_version: str,
        test_targets: Sequence[str], runner_kind: str,
        runner_image_digest: str, architecture: str, runtime_version: str,
        policy_digest: str, execution_id: str, idempotency_key: str,
        requesting_principal: str, reviewer: str, approval_id: str,
    ) -> "ExecutionRequest":
        normalized = _targets(test_targets)
        plan_digest = _canonical_digest({"schema_version": _SCHEMA_VERSION,
                                         "test_targets": list(normalized)})
        return cls(
            proposal_id, snapshot_digest, snapshot_object_digest, snapshot_version,
            normalized, plan_digest, runner_kind, runner_image_digest,
            architecture, runtime_version, policy_digest, execution_id,
            idempotency_key, requesting_principal, reviewer, approval_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "snapshot_digest": self.snapshot_digest,
            "snapshot_object_digest": self.snapshot_object_digest,
            "snapshot_version": self.snapshot_version,
            "test_targets": list(self.test_targets),
            "test_plan_digest": self.test_plan_digest,
            "runner_kind": self.runner_kind,
            "runner_image_digest": self.runner_image_digest,
            "architecture": self.architecture,
            "runtime_version": self.runtime_version,
            "policy_digest": self.policy_digest,
            "execution_id": self.execution_id,
            "idempotency_key": self.idempotency_key,
            "requesting_principal": self.requesting_principal,
            "reviewer": self.reviewer,
            "approval_id": self.approval_id,
        }

    @property
    def request_digest(self) -> str:
        return _canonical_digest(self.to_dict())


@dataclass(frozen=True)
class ExecutionEvidence:
    """Untrusted runner result envelope; signature verification is a separate gate."""

    execution_id: str
    request_digest: str
    proposal_id: str
    snapshot_digest: str
    test_plan_digest: str
    runner_image_digest: str
    policy_digest: str
    outcome: str
    exit_code: int
    started_at: str
    finished_at: str
    result_digest: str
    runner_identity: str
    issuer: str
    audience: str
    nonce: str
    key_id: str
    signature: str
    schema_version: int = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != _SCHEMA_VERSION:
            raise ValueError("schema_version_unsupported")
        for field in ("execution_id", "proposal_id", "nonce", "key_id"):
            _identifier(getattr(self, field), field)
        for field in ("runner_identity", "issuer", "audience"):
            _claim(getattr(self, field), field)
        for field in ("request_digest", "snapshot_digest", "test_plan_digest",
                      "runner_image_digest", "policy_digest", "result_digest"):
            _digest(getattr(self, field), field)
        if self.outcome not in _ALLOWED_OUTCOMES:
            raise ValueError("outcome_invalid")
        if type(self.exit_code) is not int or not -255 <= self.exit_code <= 255:
            raise ValueError("exit_code_invalid")
        if not isinstance(self.started_at, str) or not isinstance(self.finished_at, str):
            raise ValueError("timestamps_invalid")
        if not self.started_at or not self.finished_at:
            raise ValueError("timestamps_invalid")
        if not isinstance(self.signature, str) or not 1 <= len(self.signature) <= 8192:
            raise ValueError("signature_invalid")

    def verify_binding(self, request: ExecutionRequest) -> None:
        """Check request/result equality only; does not authenticate the signature."""
        expected = {
            "execution_id": request.execution_id,
            "request_digest": request.request_digest,
            "proposal_id": request.proposal_id,
            "snapshot_digest": request.snapshot_digest,
            "test_plan_digest": request.test_plan_digest,
            "runner_image_digest": request.runner_image_digest,
            "policy_digest": request.policy_digest,
        }
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError("evidence_request_binding_mismatch")

    def signed_payload(self) -> dict[str, Any]:
        """Canonical claims excluding signature, suitable for a real verifier."""
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "request_digest": self.request_digest,
            "proposal_id": self.proposal_id,
            "snapshot_digest": self.snapshot_digest,
            "test_plan_digest": self.test_plan_digest,
            "runner_image_digest": self.runner_image_digest,
            "policy_digest": self.policy_digest,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_digest": self.result_digest,
            "runner_identity": self.runner_identity,
            "issuer": self.issuer,
            "audience": self.audience,
            "nonce": self.nonce,
            "key_id": self.key_id,
        }

    @property
    def payload_digest(self) -> str:
        return _canonical_digest(self.signed_payload())
