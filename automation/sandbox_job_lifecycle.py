"""Host-owned lifecycle for isolated sandbox jobs.

This manager owns private staging directories and enforces state transitions. It
does not implement a container runtime: launch and freeze callbacks must bind the
job directory as the container's only writable output mount and must stop the
container plus revoke all writers before returning success from freeze.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Callable, Sequence

from automation.sandbox_artifacts import (
    ArtifactManifest,
    ApprovedArtifactPromotion,
    validate_artifact_directory,
)


class SandboxJobError(RuntimeError):
    """A sandbox job transition or runtime operation was rejected."""


class JobState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    FROZEN = "frozen"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLEANED = "cleaned"


@dataclass
class _Job:
    job_id: str
    staging_directory: Path
    state: JobState = JobState.CREATED
    runtime_handle: object | None = None
    manifest: ArtifactManifest | None = None
    promoted_paths: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class SandboxJobSnapshot:
    job_id: str
    staging_directory: str
    state: JobState
    manifest_digest: str | None
    promoted_paths: tuple[str, ...]


class SandboxJobLifecycle:
    """Own job staging, execution transitions, validation, promotion, and cleanup."""

    def __init__(self, jobs_root: str | Path, *,
                 launcher: Callable[[str, str], object],
                 freezer: Callable[[object], bool],
                 promotion: ApprovedArtifactPromotion,\n                 audit: Callable[[dict], None]):
        root = Path(jobs_root).expanduser()
        if root.is_symlink():
            raise ValueError("jobs root cannot be a symlink")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("jobs root must be an existing directory")
        if not callable(launcher) or not callable(freezer):
            raise TypeError("launcher and freezer callbacks are required")
        if not isinstance(promotion, ApprovedArtifactPromotion):
            raise TypeError("an approved artifact promotion service is required")
        self._root = root
        self._launcher = launcher
        self._freezer = freezer
        self._promotion = promotion
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()

    def create_job(self) -> SandboxJobSnapshot:
        """Create a unique host-owned staging directory with owner-only access."""
        import secrets

        with self._lock:
            for _ in range(5):
                job_id = "job_" + secrets.token_hex(16)
                directory = self._root / job_id
                try:
                    os.mkdir(directory, 0o700)
                    break
                except FileExistsError:
                    continue
            else:
                raise SandboxJobError("could not allocate a unique sandbox job directory")
            try:
                os.chmod(directory, 0o700)
                info = directory.lstat()
                if directory.is_symlink() or not directory.is_dir() or info.st_uid != os.getuid():
                    raise SandboxJobError("staging directory ownership or type check failed")
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            job = _Job(job_id=job_id, staging_directory=directory)
            self._jobs[job_id] = job
            return self._snapshot(job)

    def start_job(self, job_id: str) -> SandboxJobSnapshot:
        """Launch the job against only the manager-created staging directory."""
        with self._lock:
            job = self._get(job_id)
            if job.state is not JobState.CREATED:
                raise SandboxJobError("only a newly created job can start")
            try:
                handle = self._launcher(job.job_id, str(job.staging_directory))
                if handle is None:
                    raise SandboxJobError("sandbox launcher returned no runtime handle")
            except Exception as exc:
                job.state = JobState.FAILED
                raise SandboxJobError("sandbox job could not be started") from exc
            job.runtime_handle = handle
            job.state = JobState.RUNNING
            return self._snapshot(job)

    def freeze_job(self, job_id: str) -> SandboxJobSnapshot:
        """Stop the runtime and revoke writers before artifact inspection."""
        with self._lock:
            job = self._get(job_id)
            if job.state is not JobState.RUNNING or job.runtime_handle is None:
                raise SandboxJobError("only a running job can be frozen")
            try:
                frozen = self._freezer(job.runtime_handle) is True
            except Exception as exc:
                raise SandboxJobError("sandbox freeze could not be confirmed") from exc
            if not frozen:
                raise SandboxJobError("sandbox freeze was not confirmed; staging remains untrusted")
            job.runtime_handle = None
            job.state = JobState.FROZEN
            return self._snapshot(job)

    def validate_job(self, job_id: str, *,
                     allowed_extensions: Sequence[str]) -> ArtifactManifest:
        """Validate output only after the runtime confirms all writers are stopped."""
        with self._lock:
            job = self._get(job_id)
            if job.state is not JobState.FROZEN:
                raise SandboxJobError("job must be frozen before artifact validation")
            try:
                manifest = validate_artifact_directory(
                    job.staging_directory, allowed_extensions=allowed_extensions
                )
            except Exception:
                job.state = JobState.FAILED
                raise
            job.manifest = manifest
            job.state = JobState.VALIDATED
            return manifest

    def promote_artifact(self, job_id: str, *, principal,
                         artifact_path: str, destination_path: str) -> str:
        """Promote one manifest entry; each entry requires its own exact approval."""
        with self._lock:
            job = self._get(job_id)
            if job.state not in {JobState.VALIDATED, JobState.PROMOTED} or job.manifest is None:
                raise SandboxJobError("job must have a validated manifest before promotion")
            if artifact_path in job.promoted_paths:
                raise SandboxJobError("artifact has already been promoted for this job")
            digest = self._promotion.promote(
                principal=principal,
                job_id=job.job_id,
                staging_directory=job.staging_directory,
                manifest=job.manifest,
                artifact_path=artifact_path,
                destination_path=destination_path,
            )
            job.promoted_paths.add(artifact_path)
            if len(job.promoted_paths) == len(job.manifest.files):
                job.state = JobState.PROMOTED
            return digest

    def discard_job(self, job_id: str) -> SandboxJobSnapshot:
        """Cancel a job; running workloads must be frozen before discard."""
        with self._lock:
            job = self._get(job_id)
            if job.state is JobState.RUNNING:
                try:
                    frozen = self._freezer(job.runtime_handle) is True
                except Exception as exc:
                    raise SandboxJobError("cannot discard while runtime may still write") from exc
                if not frozen:
                    raise SandboxJobError("cannot discard while runtime may still write")
                job.runtime_handle = None
            if job.state is JobState.CLEANED:
                return self._snapshot(job)
            job.state = JobState.CANCELLED
            return self._snapshot(job)

    def cleanup_job(self, job_id: str) -> SandboxJobSnapshot:
        """Delete only terminal job staging data owned by this manager."""
        with self._lock:
            job = self._get(job_id)
            if job.state not in {JobState.PROMOTED, JobState.FAILED, JobState.CANCELLED}:
                raise SandboxJobError("only promoted, failed, or cancelled jobs can be cleaned")
            directory = job.staging_directory
            if directory.parent != self._root or directory.name != job.job_id:
                raise SandboxJobError("job staging path no longer matches its ownership record")
            if directory.is_symlink():
                raise SandboxJobError("job staging directory was replaced by a symlink")
            shutil.rmtree(directory)
            job.state = JobState.CLEANED
            return self._snapshot(job)

    def _record(self, job: _Job, action: str, outcome: str, **details) -> None:\n        event = {\n            "action": "sandbox.job." + action,\n            "job_id": job.job_id,\n            "outcome": outcome,\n            "state": job.state.value,\n        }\n        event.update(details)\n        try:\n            self._audit(event)\n        except Exception as exc:\n            raise SandboxJobError("lifecycle audit unavailable; operation denied") from exc\n\n    def get_job(self, job_id: str) -> SandboxJobSnapshot:
        with self._lock:
            return self._snapshot(self._get(job_id))

    def _get(self, job_id: str) -> _Job:
        if not isinstance(job_id, str) or not re.fullmatch(r"job_[0-9a-f]{32}", job_id):
            raise SandboxJobError("invalid sandbox job ID")
        job = self._jobs.get(job_id)
        if job is None:
            raise SandboxJobError("sandbox job is not owned by this manager")
        return job

    @staticmethod
    def _snapshot(job: _Job) -> SandboxJobSnapshot:
        return SandboxJobSnapshot(
            job_id=job.job_id,
            staging_directory=str(job.staging_directory),
            state=job.state,
            manifest_digest=job.manifest.digest if job.manifest else None,
            promoted_paths=tuple(sorted(job.promoted_paths)),
        )
