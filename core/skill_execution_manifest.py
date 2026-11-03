"""Strict, data-only manifest validation for future isolated skill execution.

This module validates metadata only. It does not import skills, access the filesystem,
authorize invocations, create environments, or launch processes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SKILL_ID = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}(?:[-+][A-Za-z0-9.-]+)?$")
_ENTRYPOINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,254}@sha256:[0-9a-f]{64}$")
_CAPABILITIES = frozenset({"read_staged_input", "write_staged_output"})
_FIELDS = frozenset({
    "protocol_version", "skill_id", "skill_version", "artifact_sha256",
    "entrypoint", "input_schema_sha256", "output_schema_sha256",
    "runtime_image", "environment_lock_sha256", "capabilities",
    "resource_limits", "network", "mount_policy",
})
_LIMIT_FIELDS = {
    "timeout_seconds": (1, 3600),
    "memory_mb": (64, 65536),
    "cpu_millis": (100, 64000),
    "max_processes": (1, 1024),
    "max_input_bytes": (1, 1048576),
    "max_output_bytes": (1, 1048576),
}


class SkillManifestError(ValueError):
    """Raised when a skill execution manifest is invalid."""


@dataclass(frozen=True)
class SkillExecutionManifest:
    protocol_version: int
    skill_id: str
    skill_version: str
    artifact_sha256: str
    entrypoint: str
    input_schema_sha256: str
    output_schema_sha256: str
    runtime_image: str
    environment_lock_sha256: str
    capabilities: tuple[str, ...]
    resource_limits: tuple[tuple[str, int], ...]
    network: str
    mount_policy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "artifact_sha256": self.artifact_sha256,
            "entrypoint": self.entrypoint,
            "input_schema_sha256": self.input_schema_sha256,
            "output_schema_sha256": self.output_schema_sha256,
            "runtime_image": self.runtime_image,
            "environment_lock_sha256": self.environment_lock_sha256,
            "capabilities": list(self.capabilities),
            "resource_limits": dict(self.resource_limits),
            "network": self.network,
            "mount_policy": self.mount_policy,
        }


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize JSON data deterministically; reject non-JSON and non-finite values."""
    if not isinstance(value, Mapping):
        raise SkillManifestError("manifest must be a JSON object")
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SkillManifestError("manifest is not canonicalizable JSON data") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SkillManifestError("duplicate JSON object key")
        result[key] = value
    return result


def parse_manifest_json(raw: str | bytes) -> SkillExecutionManifest:
    """Parse strict JSON, rejecting duplicate keys and non-finite constants."""
    if not isinstance(raw, (str, bytes)) or len(raw) > 65536:
        raise SkillManifestError("manifest JSON must be bounded text")
    try:
        value = json.loads(
            raw, object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                SkillManifestError("non-finite JSON numbers are forbidden")
            ),
        )
    except SkillManifestError:
        raise
    except (ValueError, UnicodeError, TypeError) as exc:
        raise SkillManifestError("invalid manifest JSON") from exc
    return validate_manifest(value)


def validate_manifest(value: Mapping[str, Any]) -> SkillExecutionManifest:
    """Validate and freeze a complete protocol-v1 manifest without side effects."""
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise SkillManifestError("manifest fields do not match protocol v1")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
        raise SkillManifestError("unsupported manifest protocol version")
    if not isinstance(value["skill_id"], str) or not _SKILL_ID.fullmatch(value["skill_id"]):
        raise SkillManifestError("invalid skill identifier")
    if not isinstance(value["skill_version"], str) or not _VERSION.fullmatch(value["skill_version"]):
        raise SkillManifestError("invalid skill version")
    for field in ("artifact_sha256", "input_schema_sha256",
                  "output_schema_sha256", "environment_lock_sha256"):
        if not isinstance(value[field], str) or not _SHA256.fullmatch(value[field]):
            raise SkillManifestError(f"{field} must be a lowercase SHA-256 digest")
    if not isinstance(value["entrypoint"], str) or not _ENTRYPOINT.fullmatch(value["entrypoint"]):
        raise SkillManifestError("entrypoint must be a relative module:function identifier")
    if not isinstance(value["runtime_image"], str) or not _IMAGE.fullmatch(value["runtime_image"]):
        raise SkillManifestError("runtime image must be pinned to a lowercase SHA-256 digest")
    capabilities = value["capabilities"]
    if (not isinstance(capabilities, list)
            or any(not isinstance(item, str) for item in capabilities)
            or len(capabilities) != len(set(capabilities))
            or capabilities != sorted(capabilities)
            or not set(capabilities) <= _CAPABILITIES):
        raise SkillManifestError("capabilities must be a sorted, unique supported list")
    limits = value["resource_limits"]
    if not isinstance(limits, Mapping) or set(limits) != set(_LIMIT_FIELDS):
        raise SkillManifestError("resource limits do not match protocol v1")
    frozen_limits = []
    for name, (minimum, maximum) in _LIMIT_FIELDS.items():
        number = limits[name]
        if type(number) is not int or not minimum <= number <= maximum:
            raise SkillManifestError(f"invalid resource limit: {name}")
        frozen_limits.append((name, number))
    if value["network"] != "none":
        raise SkillManifestError("network access is disabled in protocol v1")
    if value["mount_policy"] != "invocation_staging_only":
        raise SkillManifestError("unsupported mount policy")
    return SkillExecutionManifest(
        protocol_version=1, skill_id=value["skill_id"],
        skill_version=value["skill_version"], artifact_sha256=value["artifact_sha256"],
        entrypoint=value["entrypoint"], input_schema_sha256=value["input_schema_sha256"],
        output_schema_sha256=value["output_schema_sha256"],
        runtime_image=value["runtime_image"],
        environment_lock_sha256=value["environment_lock_sha256"],
        capabilities=tuple(capabilities), resource_limits=tuple(frozen_limits),
        network="none", mount_policy="invocation_staging_only",
    )


def manifest_sha256(manifest: SkillExecutionManifest) -> str:
    """Return the canonical digest of an already validated manifest."""
    if not isinstance(manifest, SkillExecutionManifest):
        raise SkillManifestError("validated manifest required")
    return hashlib.sha256(canonical_json_bytes(manifest.as_dict())).hexdigest()


def verify_artifact_digest(artifact: bytes, expected_sha256: str) -> bool:
    """Compare exact artifact bytes to a validated lowercase SHA-256 digest."""
    if not isinstance(artifact, bytes) or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise SkillManifestError("artifact and expected SHA-256 digest are required")
    return hashlib.sha256(artifact).hexdigest() == expected_sha256
