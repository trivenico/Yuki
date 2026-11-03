"""Immutable, fail-closed request contract for isolated skill containers.

This module builds data only. It does not invoke Docker, consume approval,
claim a lease, or execute skill code. The supervisor must revalidate the
approval and fencing token immediately before handing this request to a
separately reviewed container launcher.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from core.skill_execution_manifest import SkillExecutionManifest, manifest_sha256
from core.skill_invocation_approval import SkillInvocationBinding
from core.skill_staging import StagedSkillArtifact


class SkillContainerRequestError(ValueError):
    """The approved skill cannot be represented as a safe container request."""


@dataclass(frozen=True)
class SkillContainerRequest:
    actor_id: str
    tenant_id: str
    task_id: str
    invocation_id: str
    skill_id: str
    worker_id: str
    fencing_token: int
    binding_sha256: str
    manifest_sha256: str
    artifact_sha256: str
    staging_sha256: str
    argument_sha256: str
    entrypoint: str
    input_schema_sha256: str
    output_schema_sha256: str
    capabilities: tuple[str, ...]
    read_grants: tuple[str, ...]
    write_grants: tuple[str, ...]
    image: str
    workspace: str
    mount_target: str
    mount_read_only: bool
    command: tuple[str, ...]
    network_mode: str
    read_only_root: bool
    non_root: bool
    drop_all_capabilities: bool
    no_new_privileges: bool
    timeout_seconds: int
    memory_mb: int
    cpu_millis: int
    max_processes: int
    max_input_bytes: int
    max_output_bytes: int

    def canonical_sha256(self) -> str:
        payload = {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _tree_sha256(root: Path) -> str:
    records: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise SkillContainerRequestError("staged tree contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise SkillContainerRequestError("staged tree contains a non-regular file")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        records.append((path.relative_to(root).as_posix(), size, digest.hexdigest()))
    encoded = "\n".join(
        f"{name}\0{size}\0{digest}" for name, size, digest in records
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_skill_container_request(
    manifest: SkillExecutionManifest,
    staged: StagedSkillArtifact,
    binding: SkillInvocationBinding,
    *,
    worker_id: str,
    fencing_token: int,
) -> SkillContainerRequest:
    """Build a container request only when manifest, staged bytes and approval agree."""
    if not isinstance(manifest, SkillExecutionManifest):
        raise SkillContainerRequestError("validated skill manifest required")
    if not isinstance(staged, StagedSkillArtifact):
        raise SkillContainerRequestError("verified staged artifact required")
    if not isinstance(binding, SkillInvocationBinding):
        raise SkillContainerRequestError("approved invocation binding required")
    if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 256 or "\x00" in worker_id:
        raise SkillContainerRequestError("valid worker identity required")
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
        raise SkillContainerRequestError("valid worker fencing token required")

    expected_manifest = manifest_sha256(manifest)
    checks = (
        (binding.skill_id, manifest.skill_id),
        (binding.manifest_sha256, expected_manifest),
        (binding.artifact_sha256, manifest.artifact_sha256),
        (binding.argument_sha256, binding.argument_sha256),
        (binding.input_schema_sha256, manifest.input_schema_sha256),
        (binding.runtime_image, manifest.runtime_image),
        (binding.entrypoint, manifest.entrypoint),
        (binding.capabilities, manifest.capabilities),
        (binding.resource_limits, manifest.resource_limits),
        (binding.staging_sha256, staged.tree_sha256),
        (staged.artifact_sha256, manifest.artifact_sha256),
    )
    if any(actual != expected for actual, expected in checks):
        raise SkillContainerRequestError("manifest, staged artifact and approval binding do not match")

    root = Path(staged.root)
    if root.is_symlink():
        raise SkillContainerRequestError("staging root must not be a symlink")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise SkillContainerRequestError("staged artifact directory is unavailable") from exc
    if not resolved.is_dir():
        raise SkillContainerRequestError("staged artifact root must be a directory")
    if _tree_sha256(resolved) != staged.tree_sha256:
        raise SkillContainerRequestError("staged artifact changed after verification")
    if not os.path.isabs(str(resolved)):
        raise SkillContainerRequestError("staging path must be absolute")

    limits = dict(manifest.resource_limits)
    return SkillContainerRequest(
        actor_id=binding.actor_id,
        tenant_id=binding.tenant_id,
        task_id=binding.task_id,
        invocation_id=binding.invocation_id,
        skill_id=binding.skill_id,
        worker_id=worker_id,
        fencing_token=fencing_token,
        binding_sha256=binding.binding_sha256,
        manifest_sha256=expected_manifest,
        artifact_sha256=manifest.artifact_sha256,
        staging_sha256=staged.tree_sha256,
        argument_sha256=binding.argument_sha256,
        entrypoint=manifest.entrypoint,
        input_schema_sha256=manifest.input_schema_sha256,
        output_schema_sha256=manifest.output_schema_sha256,
        capabilities=binding.capabilities,
        read_grants=binding.read_grants,
        write_grants=binding.write_grants,
        image=manifest.runtime_image,
        workspace=str(resolved),
        mount_target="/yuki/skill",
        mount_read_only=True,
        # Fixed runtime protocol entrypoint; never accept model/manifest shell argv.
        command=("/opt/yuki/bin/skill-runner", "--protocol-v1"),
        network_mode="none",
        read_only_root=True,
        non_root=True,
        drop_all_capabilities=True,
        no_new_privileges=True,
        timeout_seconds=limits["timeout_seconds"],
        memory_mb=limits["memory_mb"],
        cpu_millis=limits["cpu_millis"],
        max_processes=limits["max_processes"],
        max_input_bytes=limits["max_input_bytes"],
        max_output_bytes=limits["max_output_bytes"],
    )
