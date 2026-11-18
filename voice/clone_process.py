"""One-shot process boundary for trusted local voice-clone providers.

This is process isolation and hard termination, not a security sandbox: child
code runs as the same OS user and may inherit network/filesystem permissions.
POSIX resource limits are applied in the child. Windows is fail-closed until a
Job Object memory/CPU limiter is implemented.
"""
from __future__ import annotations

import multiprocessing
import os
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


def _apply_posix_limits(limits: VoiceCloneWorkerLimits) -> None:
    if os.name != "posix":
        raise RuntimeError("Voice-clone resource limits are not implemented on this platform.")
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))


def _provider_process(send_conn: Any, cancel_event: Any, provider: Any,
                      sample_audio: bytes, deadline: float,
                      limits: VoiceCloneWorkerLimits) -> None:
    try:
        _apply_posix_limits(limits)
        if cancel_event.is_set():
            send_conn.send(("cancelled", None))
            return
        result = provider.create_clone(
            sample_audio, cancel_event=cancel_event, deadline_monotonic=deadline,
        )
        if cancel_event.is_set():
            send_conn.send(("cancelled", None))
            return
        # Bound before IPC serialization; parent validates the exact result again.
        from voice.clone_job import VoiceCloneOutput
        if not isinstance(result, VoiceCloneOutput):
            raise TypeError("provider result has an unsupported type")
        if (not isinstance(result.profile_artifact, bytes)
                or not 1 <= len(result.profile_artifact) <= 20 * 1024 * 1024):
            raise ValueError("provider artifact is outside the allowed size")
        if (not isinstance(result.preview_audio, bytes)
                or not 1 <= len(result.preview_audio) <= 10 * 1024 * 1024):
            raise ValueError("provider preview is outside the allowed size")
        send_conn.send(("ok", result))
    except BaseException as exc:
        # Never send exception messages, which may contain paths or sample data.
        try:
            send_conn.send(("error", type(exc).__name__))
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
                        status, payload = receive_conn.recv()
                    except (EOFError, OSError) as exc:
                        raise VoiceCloneWorkerError("voice clone worker exited without a result") from exc
                    process.join(self.terminate_grace_seconds)
                    if status == "ok":
                        return payload
                    if status == "cancelled":
                        raise VoiceCloneWorkerCancelled("voice clone worker cancelled")
                    raise VoiceCloneWorkerError(f"voice clone provider failed ({payload})")
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
