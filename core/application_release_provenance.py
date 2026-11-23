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
import os
import stat
from pathlib import Path
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


class ReadOnlyApplicationReleaseProvenanceStore:
    """Immutable, service-owned per-execution release records.

    Filenames are derived from the full image/task/worker/spec/container tuple.
    Records are created once with O_EXCL and can never be overwritten in place.
    The directory and records must be provisioned by the trusted host publisher.
    """

    def __init__(self, *, directory: str, expected_uid: int) -> None:
        if type(directory) is not str or not os.path.isabs(directory):
            raise ValueError("release provenance directory must be an absolute path.")
        if type(expected_uid) is not int or expected_uid < 0:
            raise ValueError("release provenance owner UID is invalid.")
        target = Path(directory)
        current = Path(target.anchor)
        for part in target.parts[1:]:
            current = current / part
            info = os.stat(current, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RuntimeInventoryError("release provenance store path contains a symlink or non-directory.")
        info = os.stat(target, follow_symlinks=False)
        if info.st_uid != expected_uid or info.st_mode & 0o022:
            raise RuntimeInventoryError("release provenance directory must be service-owned and not group/world writable.")
        self.directory = str(target)
        self.expected_uid = expected_uid

    @staticmethod
    def filename_for(image_ref: str, task_id: str, worker_id: str, spec_id: str, container_id: str) -> str:
        values = (image_ref, task_id, worker_id, spec_id, container_id)
        if (type(image_ref) is not str or not _IMAGE_REF.fullmatch(image_ref)
                or any(type(value) is not str or not value or len(value) > 512
                       or any(ord(char) < 32 for char in value)
                       for value in values[1:])
                or type(container_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", container_id)):
            raise RuntimeInventoryError("release provenance lookup identity is invalid.")
        digest = hashlib.sha256(_canonical_bytes({"image_ref": image_ref, "task_id": task_id,
            "worker_id": worker_id, "spec_id": spec_id, "container_id": container_id})).hexdigest()
        return digest + ".json"

    def publish(self, record: Mapping[str, object], *, container_id: str, hmac_key: bytes) -> str:
        """Atomically publish a new signed record; existing execution keys cannot be replaced."""
        if os.geteuid() != self.expected_uid:
            raise RuntimeInventoryError("release publisher must run as the configured store owner.")
        if not isinstance(record, Mapping):
            raise RuntimeInventoryError("release record must be a mapping.")
        image_ref = record.get("image_ref")
        task_id, worker_id, spec_id = record.get("task_id"), record.get("worker_id"), record.get("spec_id")
        if type(hmac_key) is not bytes or len(hmac_key) < 32:
            raise RuntimeInventoryError("release publisher signing key is invalid.")
        if not isinstance(record.get("runtime_versions"), Mapping) or not isinstance(record.get("installed_dependencies"), Mapping):
            raise RuntimeInventoryError("release record inventories are invalid.")
        verify_application_release_provenance(
            record, hmac_key=hmac_key,
            expected_application_version=record.get("application_version"),
            expected_build_id=record.get("build_id"),
            expected_image_ref=image_ref, task_id=task_id, worker_id=worker_id,
            spec_id=spec_id, runtime_versions=record.get("runtime_versions"),
            installed_dependencies=record.get("installed_dependencies"),
        )
        filename = self.filename_for(image_ref, task_id, worker_id, spec_id, container_id)
        body = _canonical_bytes(record)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(self.directory, directory_flags)
        try:
            info = os.fstat(directory_fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.expected_uid or info.st_mode & 0o022):
                raise RuntimeInventoryError("release provenance directory security changed.")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(filename, flags, 0o600, dir_fd=directory_fd)
            except FileExistsError as exc:
                raise RuntimeInventoryError("release record already exists and cannot be overwritten.") from exc
            try:
                view = memoryview(body)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise RuntimeInventoryError("release record publication was incomplete.")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return filename

    def __call__(self, image_ref: str, task_id: str, worker_id: str,
                 spec_id: str, container_id: str) -> Mapping[str, object]:
        filename = self.filename_for(image_ref, task_id, worker_id, spec_id, container_id)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(self.directory, directory_flags)
        try:
            directory_info = os.fstat(directory_fd)
            if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != self.expected_uid
                    or directory_info.st_mode & 0o022):
                raise RuntimeInventoryError("release provenance directory security changed.")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(filename, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise RuntimeInventoryError("signed application release record is missing or inaccessible.") from exc
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.expected_uid
                        or info.st_nlink != 1 or info.st_mode & 0o077
                        or info.st_size < 1 or info.st_size > _MAX_BYTES):
                    raise RuntimeInventoryError("release record ownership, mode, type or size is unsafe.")
                chunks = bytearray()
                while len(chunks) <= _MAX_BYTES:
                    chunk = os.read(fd, min(4096, _MAX_BYTES + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                if len(chunks) > _MAX_BYTES:
                    raise RuntimeInventoryError("release record exceeds its byte ceiling.")
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate release record key")
                result[key] = value
            return result
        try:
            value = json.loads(bytes(chunks).decode("ascii"), object_pairs_hook=unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeInventoryError("release record is not valid duplicate-free JSON.") from exc
        if not isinstance(value, dict):
            raise RuntimeInventoryError("release record must be a JSON object.")
        return value


def build_host_agent_release_verified_collector(
    *, inventory_collector: Callable[[str, str, str, str, str], Mapping[str, Mapping[str, str]]],
    release_record_lookup: Callable[[str, str, str, str, str], Mapping[str, object]],
    hmac_key: bytes, application_version: str, build_id: str,
) -> Callable[[str, str, str, str, str], dict[str, dict[str, str]]]:
    """Verify release provenance at the host-agent collector boundary."""
    if not callable(inventory_collector) or not callable(release_record_lookup):
        raise TypeError("trusted inventory collector and release record lookup are required.")
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise RuntimeInventoryError("release provenance verification key is invalid.")
    _validate_identity(application_version=application_version, build_id=build_id,
        image_ref="registry.invalid/yuki@sha256:" + "0" * 64)
    key = bytes(hmac_key)

    def collect(task_id: str, worker_id: str, container_id: str, image_ref: str,
                spec_id: str) -> dict[str, dict[str, str]]:
        try:
            observed = inventory_collector(task_id, worker_id, container_id, image_ref, spec_id)
            record = release_record_lookup(image_ref, task_id, worker_id, spec_id, container_id)
        except Exception as exc:
            raise RuntimeInventoryError("trusted release inventory or provenance lookup failed.") from exc
        if not isinstance(observed, Mapping) or set(observed) != {"runtime_versions", "installed_dependencies"}:
            raise RuntimeInventoryError("trusted runtime inventory has an invalid shape.")
        runtime = _validate_runtime_versions(observed["runtime_versions"])
        dependencies = validate_installed_dependency_inventory(observed["installed_dependencies"])
        verify_application_release_provenance(record, hmac_key=key,
            expected_application_version=application_version, expected_build_id=build_id,
            expected_image_ref=image_ref, task_id=task_id, worker_id=worker_id,
            spec_id=spec_id, runtime_versions=runtime, installed_dependencies=dependencies)
        return {"runtime_versions": runtime, "installed_dependencies": dependencies}
    return collect



def build_application_release_record_publisher(
    *, inventory_collector: Callable[[str, str, str, str, str], Mapping[str, Mapping[str, str]]],
    record_store: ReadOnlyApplicationReleaseProvenanceStore,
    hmac_key: bytes, application_version: str, build_id: str,
) -> Callable[[str, str, str, str, str], str]:
    """Build the trusted pre-evidence publisher used by the host launcher.

    The launcher invokes this after Docker assigns the container ID and before
    requesting authenticated runtime inventory. It independently measures the
    same pinned execution inventory that the host agent will measure again.
    """
    if not callable(inventory_collector) or not isinstance(record_store, ReadOnlyApplicationReleaseProvenanceStore):
        raise TypeError("trusted inventory collector and release record store are required.")
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise RuntimeInventoryError("release publisher signing key is invalid.")
    _validate_identity(application_version=application_version, build_id=build_id,
        image_ref="registry.invalid/yuki@sha256:" + "0" * 64)
    key = bytes(hmac_key)

    def publish(task_id: str, worker_id: str, container_id: str,
                image_ref: str, spec_id: str) -> str:
        try:
            observed = inventory_collector(task_id, worker_id, container_id, image_ref, spec_id)
        except Exception as exc:
            raise RuntimeInventoryError("trusted pre-evidence release inventory measurement failed.") from exc
        if not isinstance(observed, Mapping) or set(observed) != {"runtime_versions", "installed_dependencies"}:
            raise RuntimeInventoryError("trusted pre-evidence inventory has an invalid shape.")
        runtime = _validate_runtime_versions(observed["runtime_versions"])
        dependencies = validate_installed_dependency_inventory(observed["installed_dependencies"])
        record = create_application_release_provenance(
            application_version=application_version, build_id=build_id, image_ref=image_ref,
            task_id=task_id, worker_id=worker_id, spec_id=spec_id,
            runtime_versions=runtime, installed_dependencies=dependencies, hmac_key=key,
        )
        return record_store.publish(record, container_id=container_id, hmac_key=key)

    return publish
