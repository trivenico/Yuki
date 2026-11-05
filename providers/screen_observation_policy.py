"""Consent and scope policy for Yuki screen-observation sessions.

This module authorizes capture requests; it does not capture screens, connect
over SSH, or execute desktop actions. Capture adapters must enforce the grant
before reading pixels and must never treat SSH access as screen consent.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re


class ScreenObservationPolicyError(PermissionError):
    """Screen observation was not authorized or exceeded its scope."""


@dataclass(frozen=True)
class ScreenObservationGrant:
    grant_id: str
    owner_id: str
    device_id: str
    session_id: str
    issued_at_epoch: int
    expires_at_epoch: int
    allowed_transports: tuple[str, ...]
    max_image_bytes: int = 8 * 1024 * 1024
    allow_sensitive_content: bool = False
    allow_remote_input: bool = False
    allow_file_transfer: bool = False


def validate_grant(grant: ScreenObservationGrant) -> None:
    if not isinstance(grant, ScreenObservationGrant):
        raise ScreenObservationPolicyError("invalid_grant")
    for field in ("grant_id", "owner_id", "device_id", "session_id"):
        value = getattr(grant, field)
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ScreenObservationPolicyError("invalid_" + field)
    if type(grant.issued_at_epoch) is not int or type(grant.expires_at_epoch) is not int:
        raise ScreenObservationPolicyError("invalid_grant_time")
    if grant.issued_at_epoch < 0 or grant.expires_at_epoch <= grant.issued_at_epoch:
        raise ScreenObservationPolicyError("invalid_grant_lifetime")
    if grant.expires_at_epoch - grant.issued_at_epoch > 3600:
        raise ScreenObservationPolicyError("grant_lifetime_exceeds_one_hour")
    if not isinstance(grant.allowed_transports, tuple) or not grant.allowed_transports:
        raise ScreenObservationPolicyError("invalid_transports")
    if any(item not in {"local", "ssh"} for item in grant.allowed_transports):
        raise ScreenObservationPolicyError("unsupported_transport")
    if type(grant.max_image_bytes) is not int or not 1024 <= grant.max_image_bytes <= 32 * 1024 * 1024:
        raise ScreenObservationPolicyError("invalid_image_limit")
    for field in ("allow_sensitive_content", "allow_remote_input", "allow_file_transfer"):
        if type(getattr(grant, field)) is not bool:
            raise ScreenObservationPolicyError("invalid_" + field)


def authorize_screen_observation(
    grant: ScreenObservationGrant,
    *,
    owner_id: str,
    device_id: str,
    session_id: str,
    transport: str,
    now_epoch: int,
    image_size_bytes: int,
) -> dict:
    validate_grant(grant)
    if type(now_epoch) is not int or now_epoch < 0:
        raise ScreenObservationPolicyError("invalid_now_epoch")
    if type(image_size_bytes) is not int or image_size_bytes <= 0:
        raise ScreenObservationPolicyError("invalid_image_size")
    if now_epoch < grant.issued_at_epoch or now_epoch >= grant.expires_at_epoch:
        raise ScreenObservationPolicyError("grant_expired_or_not_yet_valid")
    if (owner_id, device_id, session_id) != (
        grant.owner_id, grant.device_id, grant.session_id
    ):
        raise ScreenObservationPolicyError("grant_scope_mismatch")
    if transport not in grant.allowed_transports:
        raise ScreenObservationPolicyError("transport_not_authorized")
    if image_size_bytes > grant.max_image_bytes:
        raise ScreenObservationPolicyError("image_size_exceeds_limit")
    return {
        "authorized": True,
        "grant_id": grant.grant_id,
        "device_id": grant.device_id,
        "session_id": grant.session_id,
        "transport": transport,
        "redaction_required": not grant.allow_sensitive_content,
        "remote_input_allowed": grant.allow_remote_input,
        "file_transfer_allowed": grant.allow_file_transfer,
        "expires_at_epoch": grant.expires_at_epoch,
        "capture_bytes_limit": grant.max_image_bytes,
    }


def screen_observation_digest(image_bytes: bytes) -> str:
    """Return a content digest for audit correlation; never log screen pixels."""
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise ValueError("invalid_image_bytes")
    return sha256(image_bytes).hexdigest()
