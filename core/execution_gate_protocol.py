"""Bounded, execution-bound authorization envelopes for image-owned sandbox gates.

This module defines the cryptographic message contract only. It does not create
or transport a Docker-mounted IPC channel; deployments must keep the HMAC key
out of task workspaces and deliver it only to the trusted gate process.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Iterable

_PROTOCOL = "yuki.execution-gate.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}$")
_MAX_TOKEN_BYTES = 32_768
_MAX_COMMAND_ARGS = 64
_MAX_ARG_BYTES = 4096
_MAX_TTL_SECONDS = 120


class ExecutionGateAuthorizationError(ValueError):
    """Authorization envelope is malformed, untrusted, stale or replayed."""


@dataclass(frozen=True, slots=True)
class ExecutionGateBinding:
    task_id: str
    worker_id: str
    container_id: str
    spec_id: str
    image: str

    def __post_init__(self) -> None:
        for name in ("task_id", "worker_id", "container_id", "spec_id"):
            value = getattr(self, name)
            if type(value) is not str or not _ID.fullmatch(value):
                raise ValueError(f"{name} must be a bounded identifier")
        if not re.fullmatch(r"[0-9a-f]{64}", self.container_id):
            raise ValueError("container_id must be the full daemon-assigned SHA-256 ID")
        if type(self.image) is not str or not _IMAGE.fullmatch(self.image):
            raise ValueError("image must be pinned by an immutable SHA-256 digest")


@dataclass(frozen=True, slots=True)
class ExecutionGateAuthorization:
    binding: ExecutionGateBinding
    nonce: str
    issued_at: int
    expires_at: int
    command: tuple[str, ...]


class ExecutionGateReplayGuard:
    """Thread-safe, bounded one-shot nonce registry for a single gate process."""

    def __init__(self, *, maximum_entries: int = 4096) -> None:
        if type(maximum_entries) is not int or not 1 <= maximum_entries <= 65536:
            raise ValueError("maximum_entries must be between 1 and 65536")
        self._maximum_entries = maximum_entries
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    def consume(self, nonce: str) -> None:
        with self._lock:
            if nonce in self._consumed:
                raise ExecutionGateAuthorizationError("authorization nonce has already been consumed")
            if len(self._consumed) >= self._maximum_entries:
                raise ExecutionGateAuthorizationError("gate replay registry is full; fail closed")
            self._consumed.add(nonce)


def _key_bytes(key: bytes) -> bytes:
    if type(key) is not bytes or len(key) < 32 or len(key) > 128:
        raise ValueError("gate HMAC key must contain between 32 and 128 bytes")
    return key


def _validate_command(command: Iterable[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)):
        raise ValueError("command must be an argv sequence, never a shell string")
    try:
        argv = tuple(command)
    except TypeError as exc:
        raise ValueError("command must be an argv sequence") from exc
    if not 1 <= len(argv) <= _MAX_COMMAND_ARGS:
        raise ValueError("command argument count is outside the permitted range")
    if any(type(arg) is not str or not arg or len(arg.encode("utf-8")) > _MAX_ARG_BYTES or "\\x00" in arg for arg in argv):
        raise ValueError("command arguments must be bounded non-empty UTF-8 strings")
    return argv


def issue_execution_gate_authorization(
    *, key: bytes, binding: ExecutionGateBinding, command: Iterable[str],
    ttl_seconds: int = 30, now: int | None = None,
) -> str:
    """Create a fresh signed authorization; the caller must independently authorize release."""
    secret = _key_bytes(key)
    if not isinstance(binding, ExecutionGateBinding):
        raise TypeError("binding must be an ExecutionGateBinding")
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
        raise ValueError("ttl_seconds must be between 1 and 120")
    issued = int(time.time()) if now is None else now
    if type(issued) is not int or issued < 0:
        raise ValueError("now must be a non-negative integer timestamp")
    payload = {
        "protocol": _PROTOCOL,
        "task_id": binding.task_id,
        "worker_id": binding.worker_id,
        "container_id": binding.container_id,
        "spec_id": binding.spec_id,
        "image": binding.image,
        "nonce": secrets.token_hex(32),
        "issued_at": issued,
        "expires_at": issued + ttl_seconds,
        "command": list(_validate_command(command)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    signature = hmac.new(secret, encoded, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(encoded).rstrip(b"=") + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
    if len(token) > _MAX_TOKEN_BYTES:
        raise ValueError("authorization token exceeds the configured byte ceiling")
    return token.decode("ascii")


def consume_execution_gate_authorization(
    *, token: str, key: bytes, expected: ExecutionGateBinding,
    replay_guard: ExecutionGateReplayGuard, now: int | None = None,
) -> ExecutionGateAuthorization:
    """Authenticate, bind, freshness-check and consume an envelope exactly once."""
    secret = _key_bytes(key)
    if type(token) is not str or not 1 <= len(token.encode("ascii", errors="ignore")) <= _MAX_TOKEN_BYTES:
        raise ExecutionGateAuthorizationError("authorization token is missing or oversized")
    if not isinstance(expected, ExecutionGateBinding) or not isinstance(replay_guard, ExecutionGateReplayGuard):
        raise TypeError("expected binding and replay guard are required")
    try:
        encoded_part, signature_part = token.split(".", 1)
        encoded = base64.urlsafe_b64decode(encoded_part + "=" * (-len(encoded_part) % 4))
        signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        if len(signature) != hashlib.sha256().digest_size:
            raise ValueError("invalid signature length")
        expected_signature = hmac.new(secret, encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("signature mismatch")
        payload = json.loads(encoded.decode("ascii"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutionGateAuthorizationError("authorization envelope is malformed or unauthenticated") from exc
    if type(payload) is not dict or set(payload) != {
        "protocol", "task_id", "worker_id", "container_id", "spec_id",
        "image", "nonce", "issued_at", "expires_at", "command",
    }:
        raise ExecutionGateAuthorizationError("authorization envelope fields are not exact")
    try:
        binding = ExecutionGateBinding(
            task_id=payload["task_id"], worker_id=payload["worker_id"],
            container_id=payload["container_id"], spec_id=payload["spec_id"],
            image=payload["image"],
        )
        command = _validate_command(payload["command"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ExecutionGateAuthorizationError("authorization binding or command is invalid") from exc
    nonce = payload["nonce"]
    issued = payload["issued_at"]
    expires = payload["expires_at"]
    clock = int(time.time()) if now is None else now
    if (
        payload["protocol"] != _PROTOCOL or binding != expected
        or type(nonce) is not str or not re.fullmatch(r"[0-9a-f]{64}", nonce)
        or type(issued) is not int or type(expires) is not int or type(clock) is not int
        or issued < 0 or expires <= issued or expires - issued > _MAX_TTL_SECONDS
        or issued > clock + 5 or expires <= clock
    ):
        raise ExecutionGateAuthorizationError("authorization is stale or bound to another execution")
    replay_guard.consume(nonce)
    return ExecutionGateAuthorization(
        binding=binding, nonce=nonce, issued_at=issued, expires_at=expires,
        command=command,
    )
