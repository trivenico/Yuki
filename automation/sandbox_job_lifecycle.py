"""Host-owned lifecycle for isolated sandbox jobs with mandatory audit events."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Callable, Sequence

from automation.sandbox_job_store import SandboxJobStore

from automation.sandbox_artifacts import (
    ArtifactManifest,
    ApprovedArtifactPromotion,
    validate_artifact_directory,
)


class SandboxJobError(RuntimeError):
    """A sandbox job transition or runtime operation was rejected."""


class JobState(str, Enum):
    CREATED = "created"
    LAUNCHING = "launching"
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
    """Own staging, execution transitions, validation, promotion, and cleanup.

    The audit callback is mandatory. If an event cannot be recorded, the
    corresponding transition is denied or the job is moved to FAILED.
    """

    def __init__(
        self,
        jobs_root: str | Path,
        *,
        launcher: Callable[[str, str], object],
        freezer: Callable[[object], bool],
        promotion: ApprovedArtifactPromotion,
        audit: Callable[[dict], None],
        store: SandboxJobStore,
    ):
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
        if not callable(audit):
            raise TypeError("a mandatory lifecycle audit callback is required")
        if not isinstance(store, SandboxJobStore):
            raise TypeError("a mandatory durable sandbox job store is required")
        self._root = root
        self._launcher = launcher
        self._freezer = freezer
        self._promotion = promotion
        self._audit = audit
        self._store = store
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()

    def _record(self, job: _Job, action: str, outcome: str, **details) -> None:
        event = {
            "action": "sandbox.job." + action,
            "job_id": job.job_id,
            "outcome": outcome,
            "state": job.state.value,
        }
        event.update(details)
        try:
            self._audit(event)
        except Exception as exc:
            raise SandboxJobError("lifecycle audit unavailable; operation denied") from exc

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
                job = _Job(job_id=job_id, staging_directory=directory)
                self._store.create(job_id=job_id, staging_directory=str(directory))
                self._record(job, "create", "succeeded")
            except Exception:
                # Preserve the directory if durable metadata was committed.
                if self._store.get(job_id) is None:
                    shutil.rmtree(directory, ignore_errors=True)
                raise
            self._jobs[job_id] = job
            return self._snapshot(job)

    def start_job(self, job_id: str) -> SandboxJobSnapshot:
        """Launch against only the manager-created staging directory."""
        with self._lock:
            job = self._get(job_id)
            if job.state is not JobState.CREATED:
                raise SandboxJobError("only a newly created job can start")
            container_name = "yuki-job-" + job.job_id[4:]
            try:
                self._store.transition(
                    job.job_id, expected_states=("created",), new_state="launching",
                    container_name=container_name,
                )
            except Exception as exc:
                raise SandboxJobError("could not durably record launch intent") from exc
            job.state = JobState.LAUNCHING
            try:
                handle = self._launcher(job.job_id, str(job.staging_directory))
                if handle is None:
                    raise SandboxJobError("sandbox launcher returned no runtime handle")
            except Exception as exc:
                # Docker may have launched before the client timed out.
                raise SandboxJobError("sandbox launch outcome is ambiguous; recovery required") from exc
            job.runtime_handle = handle
            try:
                self._store.transition(
                    job.job_id, expected_states=("launching",), new_state="running",
                    container_name=container_name,
                )
            except Exception as exc:
                raise SandboxJobError("runtime launched but durable state update failed") from exc
            job.state = JobState.RUNNING
            try:
                self._record(job, "start", "succeeded")
            except SandboxJobError:
                # Never report a clean failure if the launched workload may still run.
                try:
                    frozen = self._freezer(handle) is True
                except Exception:
                    frozen = False
                if frozen:
                    job.runtime_handle = None
                    job.state = JobState.FAILED
                raise
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
            try:
                self._store.transition(
                    job.job_id, expected_states=("running",), new_state="frozen"
                )
            except Exception as exc:
                raise SandboxJobError("runtime stopped but durable freeze update failed") from exc
            job.runtime_handle = None
            job.state = JobState.FROZEN
            self._record(job, "freeze", "succeeded")
            return self._snapshot(job)

    def validate_job(
        self, job_id: str, *, allowed_extensions: Sequence[str]
    ) -> ArtifactManifest:
        """Validate output only after all runtime writers have stopped."""
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
            try:
                self._store.transition(
                    job.job_id, expected_states=("frozen",), new_state="validated",
                    manifest_digest=manifest.digest,
                )
            except Exception as exc:
                raise SandboxJobError("validated manifest could not be durably recorded") from exc
            job.manifest = manifest
            job.state = JobState.VALIDATED
            self._record(job, "validate", "succeeded", manifest_digest=manifest.digest)
            return manifest

    def promote_artifact(
        self, job_id: str, *, principal, artifact_path: str, destination_path: str
    ) -> str:
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
            next_paths = set(job.promoted_paths)
            next_paths.add(artifact_path)
            final_state = "promoted" if len(next_paths) == len(job.manifest.files) else "validated"
            try:
                self._store.transition(
                    job.job_id, expected_states=("validated",), new_state=final_state,
                    promoted_paths=next_paths,
                )
            except Exception as exc:
                raise SandboxJobError("artifact published but durable promotion record failed") from exc
            job.promoted_paths = next_paths
            if final_state == "promoted":
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
            try:
                self._record(job, "discard", "succeeded")
            except SandboxJobError:
                job.state = JobState.FAILED
                raise
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
            self._record(job, "cleanup", "requested")
            shutil.rmtree(directory)
            job.state = JobState.CLEANED
            return self._snapshot(job)

    def get_job(self, job_id: str) -> SandboxJobSnapshot:
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
