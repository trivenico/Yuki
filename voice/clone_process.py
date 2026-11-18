"""One-shot process boundary for trusted local voice-clone providers.

This is process isolation and hard termination, not a security sandbox: child
code runs as the same OS user and may inherit network/filesystem permissions.
POSIX resource limits or Windows Job Object process/memory/CPU limits are
applied before provider execution. Neither platform currently denies network
access or provides a filesystem sandbox.
"""
from __future__ import annotations

import multiprocessing
import os
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any


class VoiceCloneWorkerError(RuntimeError):
    """The isolated provider worker failed."""


class VoiceCloneWorkerCancelled(VoiceCloneWorkerError):
    """The isolated provider worker was cancelled."""


class VoiceCloneWorkerTimeout(VoiceCloneWorkerError):
    """The isolated provider worker exceeded its wall-clock budget."""


class VoiceCloneResourceLimitError(RuntimeError):
    """A named OS resource limit could not be applied."""

    def __init__(self, limit_name: str, error_type: str, *, stage: str = "set",
                 current_soft: int | None = None, current_hard: int | None = None,
                 requested_soft: int | None = None, requested_hard: int | None = None):
        self.limit_name = limit_name
        self.error_type = error_type
        self.stage = stage
        self.current_soft = current_soft
        self.current_hard = current_hard
        self.requested_soft = requested_soft
        self.requested_hard = requested_hard
        # Numeric OS limit values are safe diagnostics; do not include paths,
        # environment values, provider details, or raw exception messages.
        super().__init__(
            f"{limit_name}:{error_type}:{stage}:"
            f"{current_soft}:{current_hard}:{requested_soft}:{requested_hard}"
        )


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
                       requested_hard: int, *, name: str) -> None:
    current_soft = current_hard = None
    stage = "get"
    try:
        current_soft, current_hard = resource_module.getrlimit(limit_id)
        hard = requested_hard
        if current_hard != resource_module.RLIM_INFINITY:
            hard = min(hard, current_hard)
        # Never raise the existing hard limit. Lower soft while retaining
        # the current hard cap, then lower hard in a second operation.
        soft = min(requested_soft, hard)
        stage = "lower-soft"
        resource_module.setrlimit(limit_id, (soft, current_hard))
        if hard != current_hard:
            stage = "lower-hard"
            resource_module.setrlimit(limit_id, (soft, hard))
    except (OSError, ValueError) as exc:
        raise VoiceCloneResourceLimitError(
            name, type(exc).__name__, stage=stage,
            current_soft=current_soft, current_hard=current_hard,
            requested_soft=requested_soft, requested_hard=requested_hard,
        ) from exc


def _apply_posix_limits(limits: VoiceCloneWorkerLimits) -> None:
    if os.name != "posix":
        raise RuntimeError("Voice-clone resource limits are not implemented on this platform.")
    import resource

    # macOS exposes RLIMIT_AS but does not reliably permit changing it.
    # RLIMIT_DATA is only a heap/data-segment bound (not total RSS or mmap),
    # and Darwin commonly caps it below our cross-platform default. Keep the
    # requested cap conservative so setrlimit does not exceed Darwin's limit.
    # Linux uses RLIMIT_AS for a stricter total virtual-address-space cap.
    memory_bytes = limits.memory_bytes
    if sys.platform == "darwin":
        memory_limit = getattr(resource, "RLIMIT_DATA", None)
        if memory_limit is None:
            raise RuntimeError("macOS does not expose a supported worker memory limit")
        memory_bytes = min(memory_bytes, 3 * 1024 * 1024 * 1024)
    else:
        memory_limit = resource.RLIMIT_AS
    _set_bounded_limit(
        resource, memory_limit, memory_bytes, memory_bytes,
        name="RLIMIT_DATA" if sys.platform == "darwin" else "RLIMIT_AS",
    )
    _set_bounded_limit(resource, resource.RLIMIT_CPU, limits.cpu_seconds,
                       limits.cpu_seconds + 1, name="RLIMIT_CPU")
    _set_bounded_limit(resource, resource.RLIMIT_FSIZE, limits.file_bytes,
                       limits.file_bytes, name="RLIMIT_FSIZE")
    _set_bounded_limit(resource, resource.RLIMIT_NOFILE, limits.open_files,
                       limits.open_files, name="RLIMIT_NOFILE")
    if hasattr(resource, "RLIMIT_CORE"):
        _set_bounded_limit(resource, resource.RLIMIT_CORE, 0, 0, name="RLIMIT_CORE")


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




def _create_windows_job(process: Any, limits: VoiceCloneWorkerLimits):
    """Assign the suspended-at-provider-gate child to a kill-on-close Job Object."""
    if os.name != "nt":
        raise RuntimeError("Windows Job Objects are available only on Windows.")
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    # PROCESS_TIME | ACTIVE_PROCESS | PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE
    limit_flags = 0x00000002 | 0x00000008 | 0x00000100 | 0x00000200 | 0x00002000
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = limit_flags
    info.BasicLimitInformation.PerProcessUserTimeLimit = limits.cpu_seconds * 10_000_000
    info.BasicLimitInformation.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = limits.memory_bytes
    info.JobMemoryLimit = limits.memory_bytes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "Could not create voice-clone Job Object.")
    process_handle = None
    try:
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise OSError(ctypes.get_last_error(), "Could not configure voice-clone Job Object.")
        # multiprocessing's private _handle is not a stable cross-version API.
        # Open a process handle with the rights required by AssignProcessToJobObject.
        process_handle = kernel32.OpenProcess(
            0x0001 | 0x0100, False, int(process.pid),
        )
        if not process_handle:
            raise OSError(ctypes.get_last_error(), "Could not open voice-clone worker process.")
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError(ctypes.get_last_error(), "Could not assign voice-clone worker to Job Object.")
        return job, kernel32
    except Exception:
        kernel32.CloseHandle(job)
        raise
    finally:
        if process_handle:
            kernel32.CloseHandle(process_handle)


def _provider_process(send_conn: Any, cancel_event: Any, provider: Any,
                      sample_audio: bytes, deadline: float,
                      limits: VoiceCloneWorkerLimits,
                      start_gate: Any) -> None:
    stage = "resource-limits"
    try:
        if os.name == "posix":
            _apply_posix_limits(limits)
        stage = "environment"
        _sanitize_worker_environment()
        stage = "startup-gate"
        if not start_gate.wait(max(0.0, deadline - time.monotonic())):
            send_conn.send_bytes(b"ERROR:WorkerStartupGateTimeout")
            return
        if cancel_event.is_set():
            send_conn.send_bytes(b"CANCELLED")
            return
        stage = "provider"
        result = provider.create_clone(
            sample_audio, cancel_event=cancel_event, deadline_monotonic=deadline,
        )
        if cancel_event.is_set():
            send_conn.send_bytes(b"CANCELLED")
            return
        stage = "output-validation"
        # Bound and validate in the child before data-only IPC; parent validates again.
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
        # Report only a fixed stage/limit and exception class; never forward provider text or tracebacks.
        if isinstance(exc, VoiceCloneResourceLimitError):
            stage = f"resource-limits-{exc.limit_name}"
            error_type = exc.args[0].split(":", 1)[-1]
        else:
            error_type = type(exc).__name__
        try:
            message = f"ERROR:{stage}:{error_type}".encode("ascii", "replace")[:128]
            send_conn.send_bytes(message)
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        send_conn.close()


class ProcessIsolatedVoiceCloneExecutor:
    """Run one provider invocation with POSIX limits or Windows Job Object limits."""

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
        if os.name not in {"posix", "nt"}:
            raise VoiceCloneWorkerError(
                "Voice cloning is disabled: enforceable worker limits are not available on this OS."
            )
        if not isinstance(sample_audio, bytes) or not 1 <= len(sample_audio) <= 10 * 1024 * 1024:
            raise ValueError("sample_audio must be bounded bytes")
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise VoiceCloneWorkerTimeout("worker deadline expired before launch")

        context = multiprocessing.get_context("spawn")
        receive_conn, send_conn = context.Pipe(duplex=False)
        child_cancel = context.Event()
        start_gate = context.Event()
        process = context.Process(
            target=_provider_process,
            args=(send_conn, child_cancel, provider, sample_audio,
                  deadline_monotonic, self.limits, start_gate),
            name="yuki-voice-clone-worker",
            daemon=True,
        )
        started = False
        job_handle = None
        job_kernel32 = None
        try:
            process.start()
            started = True
            send_conn.close()
            if os.name == "nt":
                job_handle, job_kernel32 = _create_windows_job(process, self.limits)
            start_gate.set()
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
                        # EOF can mean the child was terminated by an OS resource
                        # limit before it could send its sanitized startup error.
                        # Join briefly so the exit status is available for diagnosis.
                        process.join(self.terminate_grace_seconds)
                        raise VoiceCloneWorkerError(
                            f"voice clone worker exited ({process.exitcode}) without a result"
                        ) from exc
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
            if job_handle is not None:
                job_kernel32.CloseHandle(job_handle)

    def _stop(self, process: Any) -> None:
        if process.is_alive():
            process.terminate()
            process.join(self.terminate_grace_seconds)
        if process.is_alive():
            process.kill()
            process.join(self.terminate_grace_seconds)
