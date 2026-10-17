"""Ed25519 agent identity helpers.

Requires the optional 'cryptography' package. Private keys are caller-managed and
must never be committed. This module does not implement a centralized registry.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import time


class IdentityUnavailable(RuntimeError):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives import serialization
        return Ed25519PrivateKey, Ed25519PublicKey, serialization
    except ImportError as exc:
        raise IdentityUnavailable("Install the optional 'cryptography' package to use agent identity.") from exc


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    public_key_b64: str
    algorithm: str = "Ed25519"
    version: int = 1

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(base64.b64decode(self.public_key_b64)).hexdigest()


def generate_identity(agent_id: str) -> tuple[AgentIdentity, bytes]:
    if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 128:
        raise ValueError("agent_id must be a non-empty string of at most 128 characters")
    Private, _, serialization = _crypto()
    private = Private.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    identity = AgentIdentity(agent_id.strip(), base64.b64encode(public_raw).decode("ascii"))
    return identity, private_raw


def sign_payload(identity: AgentIdentity, private_key: bytes, payload: dict) -> dict:
    Private, _, _ = _crypto()
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    canonical = json.dumps({"identity": identity.__dict__, "payload": payload},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = Private.from_private_bytes(private_key).sign(canonical)
    return {"identity": identity.__dict__, "payload": payload,
            "signature": base64.b64encode(signature).decode("ascii"),
            "signed_at": int(time.time())}


def verify_envelope(envelope: dict, *, trusted_fingerprints: set[str]) -> bool:
    try:
        _, Public, _ = _crypto()
        identity_data = envelope["identity"]
        identity = AgentIdentity(**identity_data)
        if identity.algorithm != "Ed25519" or identity.version != 1:
            return False
        if identity.fingerprint not in trusted_fingerprints:
            return False
        canonical = json.dumps({"identity": identity.__dict__, "payload": envelope["payload"]},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        signature = base64.b64decode(envelope["signature"], validate=True)
        Public.from_public_bytes(base64.b64decode(identity.public_key_b64, validate=True)).verify(signature, canonical)
        return True
    except Exception:
        return False
