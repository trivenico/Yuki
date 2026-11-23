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
import stat
import time
import os
from pathlib import Path
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
            if runtime:
                key = name.lower()
            elif name.startswith("npm:") and "#" in name:
                # npm install paths are part of identity; normalizing dots,
                # underscores or hyphens across the path can merge distinct
                # Linux directories into one false dependency identity.
                key = name.lower()
            else:
                key = re.sub(r"[-_.]+", "-", name).lower()
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


class ReadOnlySBOMManifestStore:
    """Read digest-indexed JSON manifests from a locked-down operator directory.

    Files are named by SHA-256(image_ref), opened relative to a pinned directory
    descriptor with O_NOFOLLOW, and checked for owner, regular-file type,
    single-link identity and private permissions before bounded reading.
    """

    def __init__(self, *, directory: str, expected_uid: int,
                 maximum_file_bytes: int = _MAX_MANIFEST_BYTES) -> None:
        if type(directory) is not str or not os.path.isabs(directory):
            raise ValueError("SBOM manifest directory must be an absolute path.")
        if type(expected_uid) is not int or expected_uid < 0:
            raise ValueError("SBOM manifest owner UID is invalid.")
        if type(maximum_file_bytes) is not int or not 1 <= maximum_file_bytes <= _MAX_MANIFEST_BYTES:
            raise ValueError("SBOM manifest file ceiling is invalid.")
        target = Path(directory)
        current = Path(target.anchor)
        for part in target.parts[1:]:
            current = current / part
            info = os.stat(current, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RuntimeInventoryError("SBOM store path contains a symlink or non-directory.")
        info = os.stat(target, follow_symlinks=False)
        if info.st_uid != expected_uid or info.st_mode & 0o022:
            raise RuntimeInventoryError("SBOM store directory must be service-owned and not group/world writable.")
        self.directory = str(target)
        self.expected_uid = expected_uid
        self.maximum_file_bytes = maximum_file_bytes

    @staticmethod
    def filename_for(image_ref: str) -> str:
        if type(image_ref) is not str or not _IMAGE_REF.fullmatch(image_ref):
            raise RuntimeInventoryError("SBOM lookup requires an immutable image digest.")
        return hashlib.sha256(image_ref.encode("ascii")).hexdigest() + ".json"

    def __call__(self, image_ref: str) -> Mapping[str, Any]:
        filename = self.filename_for(image_ref)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(self.directory, directory_flags)
        try:
            directory_info = os.fstat(directory_fd)
            if (not stat.S_ISDIR(directory_info.st_mode)
                    or directory_info.st_uid != self.expected_uid
                    or directory_info.st_mode & 0o022):
                raise RuntimeInventoryError("SBOM store directory security changed after initialization.")
            try:
                fd = os.open(filename, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise RuntimeInventoryError("trusted SBOM manifest is missing or inaccessible.") from exc
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.expected_uid
                        or info.st_nlink != 1 or info.st_mode & 0o077
                        or info.st_size < 1 or info.st_size > self.maximum_file_bytes):
                    raise RuntimeInventoryError("SBOM manifest file ownership, mode, type or size is unsafe.")
                chunks = bytearray()
                while len(chunks) <= self.maximum_file_bytes:
                    chunk = os.read(fd, min(4096, self.maximum_file_bytes + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                if len(chunks) > self.maximum_file_bytes:
                    raise RuntimeInventoryError("SBOM manifest exceeds its byte ceiling.")
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON object key")
                result[key] = value
            return result

        try:
            value = json.loads(bytes(chunks).decode("ascii"), object_pairs_hook=unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeInventoryError("SBOM manifest file is not valid unambiguous JSON.") from exc
        if not isinstance(value, dict):
            raise RuntimeInventoryError("SBOM manifest root must be an object.")
        return value


def build_sbom_runtime_inventory_collector(
    *,
    sbom_collector: Callable[[str, str, str, str, str], Mapping[str, Mapping[str, str]]],
    runtime_observer: Callable[[str, str, str, str, str], Mapping[str, Mapping[str, str]]],
) -> Callable[[str, str, str, str, str], dict[str, dict[str, str]]]:
    """Require live host inventory to exactly match the verified image SBOM.

    runtime_observer is trusted operator code. It must observe the running
    container from the host/control plane without executing task-supplied
    commands or importing from the task workspace. The SBOM and live observer
    are independent inputs; either missing or malformed input fails closed.
    """
    if not callable(sbom_collector) or not callable(runtime_observer):
        raise TypeError("verified SBOM collector and trusted runtime observer are required.")

    def collect(task_id: str, worker_id: str, container_id: str,
                image_ref: str, spec_id: str) -> dict[str, dict[str, str]]:
        try:
            expected = sbom_collector(task_id, worker_id, container_id, image_ref, spec_id)
            observed = runtime_observer(task_id, worker_id, container_id, image_ref, spec_id)
        except Exception as exc:
            raise RuntimeInventoryError("verified SBOM or live runtime observation is unavailable.") from exc
        if not isinstance(expected, Mapping) or set(expected) != {"runtime_versions", "installed_dependencies"}:
            raise RuntimeInventoryError("verified SBOM inventory has an invalid shape.")
        if not isinstance(observed, Mapping) or set(observed) != {"runtime_versions", "installed_dependencies"}:
            raise RuntimeInventoryError("live runtime observer returned an invalid inventory shape.")
        expected_runtime = DigestBoundSBOMVerifier._inventory(expected["runtime_versions"], runtime=True)
        expected_packages = DigestBoundSBOMVerifier._inventory(expected["installed_dependencies"], runtime=False)
        observed_runtime = DigestBoundSBOMVerifier._inventory(observed["runtime_versions"], runtime=True)
        observed_packages = DigestBoundSBOMVerifier._inventory(observed["installed_dependencies"], runtime=False)
        if expected_runtime != observed_runtime or expected_packages != observed_packages:
            raise RuntimeInventoryError("live container runtime inventory disagrees with the verified image SBOM.")
        return {
            "runtime_versions": observed_runtime,
            "installed_dependencies": observed_packages,
        }

    return collect
