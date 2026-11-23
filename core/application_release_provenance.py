"""Signed, execution-bound application release provenance.

This is a publisher/host HMAC assertion over an immutable release identity and
its measured runtime inventory. It is not hardware attestation: key custody and
the release publisher remain part of the trusted computing base.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import json
import re
from typing import Any, Callable

from core.host_runtime_inventory import RuntimeInventoryError
from core.dependency_provenance import validate_installed_dependency_inventory

_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,255}@sha256:[0-9a-f]{64}$")
_RELEASE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
_MAX_BYTES = 32_768
_MAX_RUNTIME_ITEMS = 128
_MAX_DEPENDENCY_ITEMS = 4096


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeInventoryError("release provenance is not canonical JSON.") from exc
    if len(encoded) > _MAX_BYTES:
        raise RuntimeInventoryError("release provenance exceeds its byte ceiling.")
    return encoded


def _validate_identity(*, application_version: str, build_id: str, image_ref: str) -> None:
    if type(application_version) is not str or not _RELEASE_TEXT.fullmatch(application_version):
        raise RuntimeInventoryError("application version is invalid.")
    if type(build_id) is not str or not _RELEASE_TEXT.fullmatch(build_id):
        raise RuntimeInventoryError("application build identity is invalid.")
    if type(image_ref) is not str or not _IMAGE_REF.fullmatch(image_ref):
        raise RuntimeInventoryError("application image reference must be immutable and digest-pinned.")


def _validate_runtime_versions(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > _MAX_RUNTIME_ITEMS:
        raise RuntimeInventoryError("release runtime inventory is missing or oversized.")
    result: dict[str, str] = {}
    for key, version in value.items():
        if (type(key) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key)
                or type(version) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+!_-]{0,63}", version)):
            raise RuntimeInventoryError("release runtime inventory contains invalid entries.")
        if key in result:
            raise RuntimeInventoryError("release runtime inventory contains duplicate keys.")
        result[key] = version
    return result


def create_application_release_provenance(
    *,
    application_version: str,
    build_id: str,
    image_ref: str,
    task_id: str,
    worker_id: str,
    spec_id: str,
    runtime_versions: Mapping[str, str],
    installed_dependencies: Mapping[str, str],
    hmac_key: bytes,
) -> dict[str, object]:
    """Create a bounded signed record from trusted release and measured evidence."""
    _validate_identity(application_version=application_version, build_id=build_id, image_ref=image_ref)
    for name, value in (("task_id", task_id), ("worker_id", worker_id), ("spec_id", spec_id)):
        if type(value) is not str or not value.strip() or len(value) > 200 or any(ord(ch) < 32 for ch in value):
            raise RuntimeInventoryError(f"release provenance {name} is invalid.")
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise RuntimeInventoryError("release provenance signing key must contain at least 256 bits.")
    runtime = _validate_runtime_versions(runtime_versions)
    if not isinstance(installed_dependencies, Mapping) or len(installed_dependencies) > _MAX_DEPENDENCY_ITEMS:
        raise RuntimeInventoryError("release dependency inventory is missing or oversized.")
    dependencies = validate_installed_dependency_inventory(installed_dependencies)
    payload: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "application_version": application_version,
        "build_id": build_id,
        "image_ref": image_ref,
        "image_digest": image_ref.rsplit("@", 1)[1],
        "task_id": task_id,
        "worker_id": worker_id,
        "spec_id": spec_id,
        "runtime_versions": runtime,
        "installed_dependencies": dependencies,
    }
    signature = hmac.new(hmac_key, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    return {**payload, "signature": signature}


def verify_application_release_provenance(
    record: Mapping[str, object],
    *,
    hmac_key: bytes,
    expected_application_version: str,
    expected_build_id: str,
    expected_image_ref: str,
    task_id: str,
    worker_id: str,
    spec_id: str,
    runtime_versions: Mapping[str, str],
    installed_dependencies: Mapping[str, str],
) -> str:
    """Verify signature, release identity, execution binding and exact inventory parity."""
    _validate_identity(
        application_version=expected_application_version,
        build_id=expected_build_id,
        image_ref=expected_image_ref,
    )
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise RuntimeInventoryError("release provenance verification key is invalid.")
    if not isinstance(record, Mapping) or set(record) != {
        "schema_version", "application_version", "build_id", "image_ref",
        "image_digest", "task_id", "worker_id", "spec_id", "runtime_versions",
        "installed_dependencies", "signature",
    }:
        raise RuntimeInventoryError("release provenance fields do not match schema version 1.")
    payload = {key: value for key, value in record.items() if key != "signature"}
    signature = record.get("signature")
    if type(signature) is not str or not _SHA256.fullmatch(signature):
        raise RuntimeInventoryError("release provenance signature is malformed.")
    expected_signature = hmac.new(hmac_key, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise RuntimeInventoryError("release provenance signature verification failed.")
    if type(record.get("schema_version")) is not int or record.get("schema_version") != _SCHEMA_VERSION:
        raise RuntimeInventoryError("unsupported application release provenance schema.")
    expected_bindings = {
        "application_version": expected_application_version,
        "build_id": expected_build_id,
        "image_ref": expected_image_ref,
        "image_digest": expected_image_ref.rsplit("@", 1)[1],
        "task_id": task_id,
        "worker_id": worker_id,
        "spec_id": spec_id,
    }
    if any(record.get(key) != value for key, value in expected_bindings.items()):
        raise RuntimeInventoryError("release provenance is not bound to the expected release and execution.")
    observed_runtime = _validate_runtime_versions(runtime_versions)
    recorded_runtime = _validate_runtime_versions(record.get("runtime_versions"))
    if recorded_runtime != observed_runtime:
        raise RuntimeInventoryError("release provenance runtime versions do not match measured evidence.")
    observed_dependencies = validate_installed_dependency_inventory(installed_dependencies)
    recorded_dependencies = validate_installed_dependency_inventory(record.get("installed_dependencies"))
    if recorded_dependencies != observed_dependencies:
        raise RuntimeInventoryError("release provenance dependencies do not match measured evidence.")
    return expected_image_ref.rsplit("@", 1)[1]


def build_release_verified_runtime_inventory_provider(
    *,
    inventory_provider: Callable[[str, str, str, Mapping[str, object], Any, Any], Mapping[str, Mapping[str, str]]],
    release_record_lookup: Callable[[str, str, str, str, str], Mapping[str, object]],
    hmac_key: bytes,
    application_version: str,
    build_id: str,
) -> Callable[[str, str, str, Mapping[str, object], Any, Any], dict[str, dict[str, str]]]:
    """Gate host runtime inventory on an operator-owned, execution-bound release record.

    The lookup must read a host/service-owned immutable release-record store;
    it must never consult task-controlled paths, environment, or workspace files.
    Version, build ID and key are fixed by trusted host deployment configuration.
    Wrap the callback supplied to LinuxHostRuntimeEvidenceBuilder. Missing records
    or any release/inventory mismatch prevent evidence construction.
    """
    if not callable(inventory_provider) or not callable(release_record_lookup):
        raise TypeError("trusted inventory provider and release-record lookup are required.")
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise RuntimeInventoryError("release provenance verification key is invalid.")
    if type(application_version) is not str or not _RELEASE_TEXT.fullmatch(application_version):
        raise RuntimeInventoryError("application version is invalid.")
    if type(build_id) is not str or not _RELEASE_TEXT.fullmatch(build_id):
        raise RuntimeInventoryError("application build identity is invalid.")
    key = bytes(hmac_key)

    def collect(task_id: str, worker_id: str, container_id: str,
                container_inspect: Mapping[str, object], spec: Any, profile: Any) -> dict[str, dict[str, str]]:
        if not isinstance(container_inspect, Mapping):
            raise RuntimeInventoryError("release verification requires pinned host execution context.")
        image_ref = getattr(profile, "image", None)
        spec_id = getattr(spec, "spec_id", None)
        if type(image_ref) is not str or type(spec_id) is not str:
            raise RuntimeInventoryError("release verification context lacks immutable image or spec identity.")
        config = container_inspect.get("Config")
        if not isinstance(config, Mapping) or config.get("Image") != image_ref:
            raise RuntimeInventoryError("release verification image differs from live container configuration.")
        try:
            observed = inventory_provider(task_id, worker_id, container_id, container_inspect, spec, profile)
            record = release_record_lookup(image_ref, task_id, worker_id, spec_id, container_id)
        except Exception as exc:
            raise RuntimeInventoryError("trusted release inventory or provenance lookup failed.") from exc
        if not isinstance(observed, Mapping) or set(observed) != {"runtime_versions", "installed_dependencies"}:
            raise RuntimeInventoryError("trusted runtime inventory has an invalid shape.")
        runtime = _validate_runtime_versions(observed["runtime_versions"])
        dependencies = validate_installed_dependency_inventory(observed["installed_dependencies"])
        verify_application_release_provenance(
            record, hmac_key=key, expected_application_version=application_version,
            expected_build_id=build_id, expected_image_ref=image_ref, task_id=task_id,
            worker_id=worker_id, spec_id=spec_id, runtime_versions=runtime,
            installed_dependencies=dependencies,
        )
        return {"runtime_versions": runtime, "installed_dependencies": dependencies}

    return collect
