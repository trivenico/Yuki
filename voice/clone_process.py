"""One-shot process boundary for trusted local voice-clone providers.

This is process isolation and hard termination, not a security sandbox: child
code runs as the same OS user and may inherit network/filesystem permissions.
POSIX resource limits are applied in the child. Windows is fail-closed until a
Job Object memory/CPU limiter is implemented.
"""
from __future__ import annotations

import multiprocessing
import os
import struct
import time
from dataclasses import dataclass
from typing import Any


class VoiceCloneWorkerError(RuntimeError):
    """The isolated provider worker failed."""


class VoiceCloneWorkerCancelled(VoiceCloneWorkerError):
    """The isolated provider worker was cancelled."""


class VoiceCloneWorkerTimeout(VoiceCloneWorkerError):
    """The isolated provider worker exceeded its wall-clock budget."""


@dataclass(frozen=True)
class VoiceCloneWorkerLimits:
    memory_bytes: int = 6 * 1024 * 1024 * 1024
    cpu_seconds: int = 240
    file_bytes: int = 32 * 1024 * 1024
    open_files: int = 64

    def __post_init__(self) -> None:
        bounds = {
            "memory_bytes": (256 * 1024 * 1024, 16 * 1024 * 1024 * 1024),
            "cpu_seconds": (1, 300),
            "file_bytes": (1024 * 1024, 64 * 1024 * 1024),
            "open_files": (16, 256),
        }
        for name, (minimum, maximum) in bounds.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _set_bounded_limit(resource_module: Any, limit_id: int, requested_soft: int,
                       requested_hard: int) -> None:
    _current_soft, current_hard = resource_module.getrlimit(limit_id)
    hard = requested_hard
    if current_hard != resource_module.RLIM_INFINITY:
        hard = min(hard, current_hard)
    soft = min(requested_soft, hard)
    resource_module.setrlimit(limit_id, (soft, hard))


def _apply_posix_limits(limits: VoiceCloneWorkerLimits) -> None:
    if os.name != "posix":
        raise RuntimeError("Voice-clone resource limits are not implemented on this platform.")
    import resource

    _set_bounded_limit(resource, resource.RLIMIT_AS, limits.memory_bytes, limits.memory_bytes)
    _set_bounded_limit(resource, resource.RLIMIT_CPU, limits.cpu_seconds, limits.cpu_seconds + 1)
    _set_bounded_limit(resource, resource.RLIMIT_FSIZE, limits.file_bytes, limits.file_bytes)
    _set_bounded_limit(resource, resource.RLIMIT_NOFILE, limits.open_files, limits.open_files)
    if hasattr(resource, "RLIMIT_CORE"):
        _set_bounded_limit(resource, resource.RLIMIT_CORE, 0, 0)


def _sanitize_worker_environment() -> None:
    allowed = {
        "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL",
        "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "OMP_NUM_THREADS",
        "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    }
    safe_environment = {key: value for key, value in os.environ.items() if key in allowed}
    os.environ.clear()
    os.environ.update(safe_environment)
    os.umask(0o077)


def _provider_process(send_conn: Any, cancel_event: Any, provider: Any,
                      sample_audio: bytes, deadline: float,
                      limits: VoiceCloneWorkerLimits) -> None:
    try:
        _apply_posix_limits(limits)
        _sanitize_worker_environment()
        if cancel_event.is_set():
            send_conn.send_bytes(b"CANCELLED")
            return
        result = provider.create_clone(
            sample_audio, cancel_event=cancel_event, deadline_monotonic=deadline,
        )
        if cancel_event.is_set():
            send_conn.send_bytes(b"CANCELLED")
            return
        # Bound before data-only IPC; parent validates the exact result again.
        from voice.clone_job import VoiceCloneOutput
        if not isinstance(result, VoiceCloneOutput):
            raise TypeError("provider result has an unsupported type")
        if (not isinstance(result.profile_artifact, bytes)
                or not 1 <= len(result.profile_artifact) <= 20 * 1024 * 1024):
            raise ValueError("provider artifact is outside the allowed size")
        if (not isinstance(result.preview_audio, bytes)
                or not 1 <= len(result.preview_audio) <= 10 * 1024 * 1024):
            raise ValueError("provider preview is outside the allowed size")
        payload = (
            b"OK"
            + struct.pack("!II", len(result.profile_artifact), len(result.preview_audio))
            + result.profile_artifact
            + result.preview_audio
        )
        send_conn.send_bytes(payload)
    except BaseException as exc:
        # Data-only IPC: never unpickle provider-controlled objects in the parent.
        try:
            send_conn.send_bytes(("ERROR:" + type(exc).__name__).encode("ascii", "replace")[:128])
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        send_conn.close()


class ProcessIsolatedVoiceCloneExecutor:
    """Run one provider invocation in a killable child process with POSIX limits."""

    def __init__(self, *, limits: VoiceCloneWorkerLimits | None = None,
                 poll_interval: float = 0.05, terminate_grace_seconds: float = 1.0):
        self.limits = limits or VoiceCloneWorkerLimits()
        if not 0.01 <= poll_interval <= 0.5:
            raise ValueError("poll_interval must be between 0.01 and 0.5 seconds")
        if not 0.1 <= terminate_grace_seconds <= 5:
            raise ValueError("terminate_grace_seconds must be between 0.1 and 5 seconds")
        self.poll_interval = poll_interval
        self.terminate_grace_seconds = terminate_grace_seconds

    def run(self, provider: Any, sample_audio: bytes, *,
            cancel_event: Any, deadline_monotonic: float):
        if os.name != "posix":
            raise VoiceCloneWorkerError(
                "Voice cloning is disabled: enforceable worker resource limits are not available on this OS."
            )
        if not isinstance(sample_audio, bytes) or not 1 <= len(sample_audio) <= 10 * 1024 * 1024:
            raise ValueError("sample_audio must be bounded bytes")
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise VoiceCloneWorkerTimeout("worker deadline expired before launch")

        context = multiprocessing.get_context("spawn")
        receive_conn, send_conn = context.Pipe(duplex=False)
        child_cancel = context.Event()
        process = context.Process(
            target=_provider_process,
            args=(send_conn, child_cancel, provider, sample_audio,
                  deadline_monotonic, self.limits),
            name="yuki-voice-clone-worker",
            daemon=True,
        )
        started = False
        try:
            process.start()
            started = True
            send_conn.close()
            while True:
                if cancel_event.is_set():
                    child_cancel.set()
                    self._stop(process)
                    raise VoiceCloneWorkerCancelled("voice clone worker cancelled")
                remaining = deadline_monotonic - time.monotonic()
                if remaining <= 0:
                    child_cancel.set()
                    self._stop(process)
                    raise VoiceCloneWorkerTimeout("voice clone worker timed out")
                if receive_conn.poll(min(self.poll_interval, remaining)):
                    try:
                        payload = receive_conn.recv_bytes(maxlength=30 * 1024 * 1024 + 10)
                    except (EOFError, OSError) as exc:
                        raise VoiceCloneWorkerError("voice clone worker exited without a result") from exc
                    process.join(self.terminate_grace_seconds)
                    if payload == b"CANCELLED":
                        raise VoiceCloneWorkerCancelled("voice clone worker cancelled")
                    if payload.startswith(b"ERROR:"):
                        error_type = payload[6:134].decode("ascii", "replace")
                        raise VoiceCloneWorkerError(f"voice clone provider failed ({error_type})")
                    if not payload.startswith(b"OK") or len(payload) < 10:
                        raise VoiceCloneWorkerError("voice clone worker returned an invalid frame")
                    artifact_size, preview_size = struct.unpack("!II", payload[2:10])
                    if (not 1 <= artifact_size <= 20 * 1024 * 1024
                            or not 1 <= preview_size <= 10 * 1024 * 1024
                            or len(payload) != 10 + artifact_size + preview_size):
                        raise VoiceCloneWorkerError("voice clone worker returned invalid output sizes")
                    from voice.clone_job import VoiceCloneOutput
                    artifact_end = 10 + artifact_size
                    return VoiceCloneOutput(payload[10:artifact_end], payload[artifact_end:])
                if not process.is_alive() and not receive_conn.poll():
                    raise VoiceCloneWorkerError(f"voice clone worker exited ({process.exitcode})")
        finally:
            receive_conn.close()
            if started:
                if process.is_alive():
                    self._stop(process)
                process.close()
            else:
                send_conn.close()

    def _stop(self, process: Any) -> None:
        if process.is_alive():
            process.terminate()
            process.join(self.terminate_grace_seconds)
        if process.is_alive():
            process.kill()
            process.join(self.terminate_grace_seconds)
