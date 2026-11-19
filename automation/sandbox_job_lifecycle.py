"""Host-owned lifecycle for isolated sandbox jobs with mandatory audit events."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import threading
from typing import Callable, Sequence

from automation.sandbox_job_store import SandboxJobStore

from automation.sandbox_artifacts import (
    ArtifactFile,
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
                    try:
                        self._store.transition(
                            job.job_id, expected_states=("running",), new_state="failed"
                        )
                    except Exception as exc:
                        raise SandboxJobError(
                            "runtime stopped after audit failure but durable state needs reconciliation"
                        ) from exc
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
            except Exception as exc:
                try:
                    self._store.transition(
                        job.job_id, expected_states=("frozen",), new_state="failed"
                    )
                except Exception as persist_exc:
                    raise SandboxJobError(
                        "artifact validation failed and durable failure state needs reconciliation"
                    ) from persist_exc
                job.state = JobState.FAILED
                raise
            try:
                self._store.transition(
                    job.job_id, expected_states=("frozen",), new_state="validated",
                    manifest_digest=manifest.digest,
                    manifest_json=json.dumps({
                        "files": [
                            {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
                            for item in manifest.files
                        ],
                        "total_bytes": manifest.total_bytes,
                        "digest": manifest.digest,
                        "allowed_extensions": sorted({Path(item.path).suffix.lower() for item in manifest.files}),
                    }, sort_keys=True, separators=(",", ":")),
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
            item = next((entry for entry in job.manifest.files if entry.path == artifact_path), None)
            if item is None:
                raise SandboxJobError("artifact is not part of the validated manifest")
            approval_data = {
                "job_id": job.job_id,
                "manifest_digest": job.manifest.digest,
                "artifact_path": artifact_path,
                "artifact_sha256": item.sha256,
                "destination_path": destination_path,
            }
            approval_digest = hashlib.sha256(
                json.dumps(approval_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            try:
                intent = self._store.record_promotion_intent(
                    job_id=job.job_id,
                    artifact_path=artifact_path,
                    destination_path=destination_path,
                    artifact_sha256=item.sha256,
                    artifact_size=item.size_bytes,
                    approval_digest=approval_digest,
                    actor_id=principal.actor_id,
                )
            except Exception as exc:
                raise SandboxJobError("promotion intent could not be durably recorded") from exc

            destination = self._promotion.root / destination_path
            try:
                cursor = self._promotion.root
                for part in Path(destination_path).parts[:-1]:
                    cursor = cursor / part
                    if cursor.is_symlink() or not cursor.is_dir():
                        raise SandboxJobError("promotion destination parent is not a trusted directory")
                destination_parent = destination.parent.resolve(strict=True)
                destination_parent.relative_to(self._promotion.root)
                if destination.is_symlink():
                    raise SandboxJobError("promotion destination is a symlink")
                if destination.exists():
                    info = destination.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != item.size_bytes:
                        raise SandboxJobError("existing promotion destination is not the expected regular file")
                    digest_check = hashlib.sha256()
                    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(str(destination), flags)
                    try:
                        opened = os.fstat(descriptor)
                        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                            raise SandboxJobError("promotion destination changed during reconciliation")
                        while True:
                            chunk = os.read(descriptor, 1024 * 1024)
                            if not chunk:
                                break
                            digest_check.update(chunk)
                    finally:
                        os.close(descriptor)
                    if digest_check.hexdigest() != item.sha256:
                        raise SandboxJobError("existing promotion destination does not match approved artifact")
                    if intent["status"] != "completed":
                        self._store.complete_promotion(
                            job_id=job.job_id, artifact_path=artifact_path,
                            approval_digest=approval_digest,
                        )
                    digest = approval_digest
                else:
                    if intent["status"] == "completed":
                        raise SandboxJobError("completed promotion destination is missing; manual reconciliation required")
                    digest = self._promotion.promote(
                        principal=principal,
                        job_id=job.job_id,
                        staging_directory=job.staging_directory,
                        manifest=job.manifest,
                        artifact_path=artifact_path,
                        destination_path=destination_path,
                    )
                    if digest != approval_digest:
                        raise SandboxJobError("promotion service returned an unexpected approval digest")
                    self._store.complete_promotion(
                        job_id=job.job_id, artifact_path=artifact_path,
                        approval_digest=approval_digest,
                    )
            except SandboxJobError:
                raise
            except Exception as exc:
                raise SandboxJobError("promotion requires reconciliation; durable intent retained") from exc

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
            previous = job.state.value
            if previous not in {"created", "running", "frozen"}:
                raise SandboxJobError("job state cannot be discarded safely")
            try:
                self._store.transition(
                    job.job_id, expected_states=(previous,), new_state="cancelled"
                )
            except Exception as exc:
                raise SandboxJobError("discard could not be durably recorded") from exc
            job.state = JobState.CANCELLED
            self._record(job, "discard", "succeeded")
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
            if directory.exists():
                if not directory.is_dir():
                    raise SandboxJobError("job staging path is no longer a directory")
                shutil.rmtree(directory)
            try:
                self._store.transition(
                    job.job_id, expected_states=(job.state.value,), new_state="cleaned"
                )
            except Exception as exc:
                raise SandboxJobError(
                    "staging removed but durable cleanup state needs reconciliation"
                ) from exc
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
            record = self._store.get(job_id)
            if record is None:
                raise SandboxJobError("sandbox job is not owned by this manager")
            try:
                state = JobState(record.state)
            except ValueError as exc:
                raise SandboxJobError("durable job state is unsupported") from exc
            directory = Path(record.staging_directory)
            if directory.parent != self._root or directory.name != job_id:
                raise SandboxJobError("durable staging path does not match manager root")
            if directory.exists() or directory.is_symlink():
                info = directory.lstat()
                if directory.is_symlink() or not directory.is_dir():
                    raise SandboxJobError("durable staging directory is not a real directory")
                if info.st_uid != os.getuid() or (info.st_mode & 0o777) != 0o700:
                    raise SandboxJobError("durable staging directory ownership or mode is unsafe")
            elif state not in {JobState.CLEANED, JobState.FAILED, JobState.CANCELLED}:
                raise SandboxJobError("durable staging directory is missing")
            manifest = None
            if state in {JobState.VALIDATED, JobState.PROMOTED} and not directory.exists():
                raise SandboxJobError("validated staging directory is missing")
            if state in {JobState.VALIDATED, JobState.PROMOTED}:
                if not record.manifest_json:
                    raise SandboxJobError("durable validated job has no manifest")
                try:
                    payload = json.loads(record.manifest_json)
                    expected_manifest = ArtifactManifest(
                        files=tuple(ArtifactFile(**item) for item in payload["files"]),
                        total_bytes=payload["total_bytes"],
                        digest=payload["digest"],
                    )
                    current_manifest = validate_artifact_directory(
                        directory, allowed_extensions=payload["allowed_extensions"]
                    )
                except Exception as exc:
                    raise SandboxJobError("durable artifact manifest could not be revalidated") from exc
                if current_manifest != expected_manifest or record.manifest_digest != expected_manifest.digest:
                    raise SandboxJobError("staging contents no longer match the durable manifest")
                manifest = current_manifest
            job = _Job(
                job_id=job_id,
                staging_directory=directory,
                state=state,
                manifest=manifest,
                promoted_paths=set(record.promoted_paths),
            )
            self._jobs[job_id] = job
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
