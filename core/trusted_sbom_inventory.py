"""Digest-bound verification for operator-published software inventory manifests.

The manifest MAC authenticates the publisher's inventory assertion. It is not
a hardware attestation or a public-key signature. Use a dedicated publisher key
that is separate from the host-agent IPC key, and provision manifests from a
trusted build/release pipeline. No task container code is executed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from typing import Any, Callable

from core.host_runtime_inventory import RuntimeInventoryError

_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,255}@sha256:[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED = {
    "schema_version", "image_ref", "generated_at", "expires_at",
    "runtime_versions", "installed_dependencies", "signature",
}
_MAX_MANIFEST_BYTES = 32_768
_MAX_ITEMS = 128


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeInventoryError("SBOM manifest is not canonical JSON.") from exc
    if not encoded or len(encoded) > _MAX_MANIFEST_BYTES:
        raise RuntimeInventoryError("SBOM manifest exceeds its byte ceiling.")
    return encoded


def sign_sbom_manifest(unsigned_manifest: Mapping[str, Any], publisher_key: bytes) -> str:
    """Create a SHA-256 HMAC for trusted build-pipeline publishing/tests."""
    if type(publisher_key) is not bytes or len(publisher_key) < 32:
        raise ValueError("SBOM publisher key must contain at least 256 bits.")
    if not isinstance(unsigned_manifest, Mapping) or "signature" in unsigned_manifest:
        raise ValueError("unsigned SBOM manifest mapping is required.")
    return hmac.new(publisher_key, _canonical_bytes(unsigned_manifest), hashlib.sha256).hexdigest()


class DigestBoundSBOMVerifier:
    """Verify freshness, publisher MAC, schema and exact immutable image binding."""

    def __init__(
        self, *, publisher_key: bytes, clock: Callable[[], float] = time.time,
        maximum_age_seconds: int = 7 * 24 * 60 * 60,
        maximum_clock_skew_seconds: int = 300,
    ) -> None:
        if type(publisher_key) is not bytes or len(publisher_key) < 32:
            raise ValueError("SBOM publisher key must contain at least 256 bits.")
        if not callable(clock):
            raise TypeError("trusted clock must be callable.")
        if type(maximum_age_seconds) is not int or not 1 <= maximum_age_seconds <= 90 * 24 * 60 * 60:
            raise ValueError("maximum_age_seconds is outside supported bounds.")
        if type(maximum_clock_skew_seconds) is not int or not 0 <= maximum_clock_skew_seconds <= 3600:
            raise ValueError("maximum_clock_skew_seconds is outside supported bounds.")
        self._key = bytes(publisher_key)
        self._clock = clock
        self.maximum_age_seconds = maximum_age_seconds
        self.maximum_clock_skew_seconds = maximum_clock_skew_seconds

    @staticmethod
    def _inventory(value: object, *, runtime: bool) -> dict[str, str]:
        if not isinstance(value, Mapping) or len(value) > _MAX_ITEMS:
            raise RuntimeInventoryError("SBOM inventory must be a bounded mapping.")
        normalized: dict[str, str] = {}
        for name, version in value.items():
            if (type(name) is not str or type(version) is not str
                    or not name or len(name) > 128 or not version or len(version) > 64
                    or any(ord(ch) < 33 or ord(ch) > 126 for ch in name + version)):
                raise RuntimeInventoryError("SBOM inventory contains invalid package metadata.")
            key = name.lower() if runtime else re.sub(r"[-_.]+", "-", name).lower()
            if key in normalized:
                raise RuntimeInventoryError("SBOM inventory contains duplicate normalized names.")
            normalized[key] = version
        return normalized

    def verify(self, manifest: Mapping[str, Any], *, expected_image_ref: str) -> dict[str, dict[str, str]]:
        if type(expected_image_ref) is not str or not _IMAGE_REF.fullmatch(expected_image_ref):
            raise RuntimeInventoryError("expected image must be pinned to an immutable SHA-256 digest.")
        if not isinstance(manifest, Mapping) or set(manifest) != _REQUIRED:
            raise RuntimeInventoryError("SBOM manifest schema is invalid.")
        unsigned = {key: value for key, value in manifest.items() if key != "signature"}
        signature = manifest.get("signature")
        if type(signature) is not str or not _SIGNATURE.fullmatch(signature):
            raise RuntimeInventoryError("SBOM publisher signature is malformed.")
        expected = hmac.new(self._key, _canonical_bytes(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise RuntimeInventoryError("SBOM publisher signature verification failed.")
        if manifest.get("schema_version") != 1 or manifest.get("image_ref") != expected_image_ref:
            raise RuntimeInventoryError("SBOM manifest is not bound to the requested immutable image.")
        generated, expires = manifest.get("generated_at"), manifest.get("expires_at")
        if type(generated) is not int or type(expires) is not int or expires <= generated:
            raise RuntimeInventoryError("SBOM manifest timestamps are invalid.")
        now = self._clock()
        if type(now) not in (int, float) or isinstance(now, bool):
            raise RuntimeInventoryError("trusted clock returned an invalid timestamp.")
        if generated > now + self.maximum_clock_skew_seconds:
            raise RuntimeInventoryError("SBOM manifest was generated in the future.")
        if now - generated > self.maximum_age_seconds or expires <= now:
            raise RuntimeInventoryError("SBOM manifest is stale or expired.")
        if expires - generated > self.maximum_age_seconds:
            raise RuntimeInventoryError("SBOM manifest validity exceeds the configured limit.")
        return {
            "runtime_versions": self._inventory(manifest.get("runtime_versions"), runtime=True),
            "installed_dependencies": self._inventory(manifest.get("installed_dependencies"), runtime=False),
        }


def build_sbom_inventory_collector(
    *, manifest_lookup: Callable[[str], Mapping[str, Any]],
    verifier: DigestBoundSBOMVerifier,
) -> Callable[[str, str, str, str, str], dict[str, dict[str, str]]]:
    """Adapt a trusted digest-indexed manifest store to the host-agent collector.

    The lookup receives only the immutable image reference. Missing manifests,
    lookup failures, and all verification failures propagate and fail closed.
    """
    if not callable(manifest_lookup) or not isinstance(verifier, DigestBoundSBOMVerifier):
        raise TypeError("trusted manifest lookup and SBOM verifier are required.")

    def collect(_task_id: str, _worker_id: str, _container_id: str,
                image_ref: str, _spec_id: str) -> dict[str, dict[str, str]]:
        try:
            manifest = manifest_lookup(image_ref)
        except Exception as exc:
            raise RuntimeInventoryError("trusted SBOM manifest lookup failed.") from exc
        return verifier.verify(manifest, expected_image_ref=image_ref)

    return collect
