"""Provider-neutral, consent-gated voice-clone job orchestration.

This controller is synchronous and must run on a dedicated worker. It does not
claim to sandbox or forcibly terminate provider code; process isolation remains
a separate release gate.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from voice.audio_validation import VoiceSampleMetadata, validate_pcm_audio, validate_voice_sample
from voice.profile_registry import VoiceProfileRegistry
from voice.secure_artifact_store import EncryptedVoiceArtifactStore
from voice.clone_process import ProcessIsolatedVoiceCloneExecutor, VoiceCloneWorkerCancelled, VoiceCloneWorkerError, VoiceCloneWorkerTimeout


class VoiceCloneJobError(RuntimeError):
    """Base error for voice-cloning job orchestration."""


class VoiceCloneJobCancelled(VoiceCloneJobError):
    """Raised when the user cancels a voice-cloning job."""


class VoiceCloneJobTimeout(VoiceCloneJobError):
    """Raised when a provider returns after the job deadline."""


class VoiceCloneJobState(str, Enum):
    IDLE = "idle"
    VALIDATING = "validating"
    GENERATING = "generating"
    STORING = "storing"
    PREVIEW_READY = "preview_ready"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class VoiceCloneOutput:
    profile_artifact: bytes
    preview_audio: bytes


@dataclass(frozen=True)
class VoiceCloneJobResult:
    job_id: str
    profile_id: str
    artifact_ref: str
    preview_audio: bytes
    sample_metadata: VoiceSampleMetadata


class LocalVoiceCloneProvider(Protocol):
    provider_id: str
    is_local: bool

    def create_clone(
        self,
        sample_audio: bytes,
        *,
        cancel_event: threading.Event,
        deadline_monotonic: float,
    ) -> VoiceCloneOutput:
        """Create an opaque reusable artifact and preview; honor cancellation."""
        ...


class VoiceCloneJobController:
    """Run one clone job and leave its profile staged for audition.

    The provider must be reviewed, local, and cooperative: it must honor the
    cancellation event and deadline. This controller cannot preempt a stuck
    in-process provider. A process-isolated executor is required for release.
    """

    def __init__(
        self,
        provider: LocalVoiceCloneProvider,
        artifact_store: EncryptedVoiceArtifactStore,
        profile_registry: VoiceProfileRegistry,
        *,
        timeout_seconds: int = 180,
        executor: ProcessIsolatedVoiceCloneExecutor | None = None,
    ):
        if getattr(provider, "is_local", None) is not True:
            raise ValueError("Only an explicitly local voice-cloning provider is supported.")
        provider_id = getattr(provider, "provider_id", None)
        if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 80:
            raise ValueError("Provider must expose a valid provider_id.")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 1 and 300.")
        self._provider = provider
        self._store = artifact_store
        self._registry = profile_registry
        self._timeout = timeout_seconds
        self._executor = executor or ProcessIsolatedVoiceCloneExecutor()
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._state = VoiceCloneJobState.IDLE
        self._active_job: str | None = None

    @property
    def state(self) -> VoiceCloneJobState:
        with self._lock:
            return self._state

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            return self._active_job

    def cancel(self) -> bool:
        """Request cooperative cancellation; false means no active job."""
        with self._lock:
            if self._active_job is None or self._state in {
                VoiceCloneJobState.PREVIEW_READY,
                VoiceCloneJobState.CANCELLED,
                VoiceCloneJobState.FAILED,
            }:
                return False
            self._cancel.set()
            return True

    def _check_cancelled(self, deadline: float) -> None:
        if self._cancel.is_set():
            raise VoiceCloneJobCancelled("Voice-cloning job was cancelled.")
        if time.monotonic() > deadline:
            raise VoiceCloneJobTimeout("Voice-cloning provider exceeded its time budget.")

    def run(
        self,
        sample_audio: bytes,
        *,
        profile_id: str,
        display_name: str,
        source_owner_consent: bool,
    ) -> VoiceCloneJobResult:
        """Validate, generate, encrypt and stage; never activate the profile."""
        with self._lock:
            if self._active_job is not None:
                raise VoiceCloneJobError("A voice-cloning job is already active.")
            self._active_job = uuid.uuid4().hex
            job_id = self._active_job
            self._cancel.clear()
            self._state = VoiceCloneJobState.VALIDATING

        artifact_ref: str | None = None
        deadline = time.monotonic() + self._timeout
        try:
            metadata = validate_voice_sample(sample_audio, source_owner_consent=source_owner_consent)
            self._check_cancelled(deadline)
            with self._lock:
                self._state = VoiceCloneJobState.GENERATING
            try:
                output = self._executor.run(
                    self._provider, sample_audio,
                    cancel_event=self._cancel, deadline_monotonic=deadline,
                )
            except VoiceCloneWorkerCancelled as exc:
                raise VoiceCloneJobCancelled("Voice-cloning job was cancelled.") from exc
            except VoiceCloneWorkerTimeout as exc:
                raise VoiceCloneJobTimeout("Voice-cloning worker exceeded its time budget.") from exc
            except VoiceCloneWorkerError as exc:
                raise VoiceCloneJobError("Isolated voice-cloning worker failed.") from exc
            self._check_cancelled(deadline)
            if not isinstance(output, VoiceCloneOutput):
                raise VoiceCloneJobError("Local provider returned an invalid result.")
            if not isinstance(output.profile_artifact, bytes) or not output.profile_artifact:
                raise VoiceCloneJobError("Local provider returned an empty profile artifact.")
            if len(output.profile_artifact) > 20 * 1024 * 1024:
                raise VoiceCloneJobError("Generated profile artifact exceeds the 20 MiB limit.")
            if not isinstance(output.preview_audio, bytes) or not output.preview_audio:
                raise VoiceCloneJobError("Local provider returned an empty preview.")
            try:
                validate_pcm_audio(output.preview_audio, max_bytes=10 * 1024 * 1024)
            except ValueError as exc:
                raise VoiceCloneJobError("Generated preview must be valid bounded PCM WAV audio.") from exc
            self._check_cancelled(deadline)
            with self._lock:
                self._state = VoiceCloneJobState.STORING
            artifact_ref = self._store.put(
                output.profile_artifact, source_owner_consent=source_owner_consent,
            )
            self._check_cancelled(deadline)
            with self._lock:
                # Serialize final cancellation check and profile commit against cancel().
                self._check_cancelled(deadline)
                self._registry.stage_profile(
                    profile_id, display_name, self._provider.provider_id, artifact_ref,
                    source_owner_consent=source_owner_consent,
                )
                self._state = VoiceCloneJobState.PREVIEW_READY
            return VoiceCloneJobResult(
                job_id, profile_id, artifact_ref, output.preview_audio, metadata,
            )
        except VoiceCloneJobCancelled:
            with self._lock:
                self._state = VoiceCloneJobState.CANCELLED
            if artifact_ref is not None:
                try:
                    self._store.delete(artifact_ref, confirmed=True)
                except Exception:
                    # Preserve cancellation as the primary outcome. The encrypted
                    # artifact remains inaccessible without the authenticated store.
                    pass
            raise
        except Exception:
            with self._lock:
                self._state = VoiceCloneJobState.FAILED
            if artifact_ref is not None:
                try:
                    self._store.delete(artifact_ref, confirmed=True)
                except Exception:
                    pass
            raise
        finally:
            with self._lock:
                self._active_job = None
