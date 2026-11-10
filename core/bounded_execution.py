"""Bounded subprocess supervisor for sandbox launchers.

This supervises the Docker CLI or another trusted launcher. It is not itself a sandbox:
OS isolation must be configured by the launcher and runtime. Output is drained continuously
but only a bounded prefix is retained. Timeouts trigger process termination.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Sequence


class ExecutionError(RuntimeError):
    """Invalid invocation or failure to start the sandbox launcher."""


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool
    output_truncated: bool


def _drain(stream, sink: bytearray, limit: int, lock: threading.Lock,
           truncation: list[bool]) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            with lock:
                remaining = max(0, limit - len(sink))
                if remaining:
                    sink.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncation[0] = True
    finally:
        stream.close()


def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        elif os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP helps isolate the process group, but Python's
            # stdlib cannot reliably kill arbitrary descendant trees on all Windows
            # versions. Production Windows deployment should use a Job Object backend.
            proc.kill()
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def run_bounded(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> ExecutionResult:
    """Run an argv vector with bounded retained output and a hard timeout.

    The caller must pass an explicit environment allowlist when using this for
    untrusted workloads. No shell is used. stdout/stderr each have a cap, so total
    retained output is at most 2 * max_output_bytes.
    """
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ExecutionError("argv must be a non-empty sequence")
    if any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
        raise ExecutionError("argv entries must be non-empty NUL-free strings")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ExecutionError("timeout_seconds must be numeric")
    if not 0 < timeout_seconds <= 3600:
        raise ExecutionError("timeout_seconds is outside the permitted range")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int):
        raise ExecutionError("max_output_bytes must be an integer")
    if not 1 <= max_output_bytes <= 16_777_216:
        raise ExecutionError("max_output_bytes is outside the permitted range")
    if cwd is not None and (not isinstance(cwd, str) or not os.path.isabs(cwd)):
        raise ExecutionError("cwd must be an absolute path")
    if env is not None:
        if not isinstance(env, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or "\x00" in k + v
            for k, v in env.items()
        ):
            raise ExecutionError("env must be a string-to-string mapping")

    start = time.monotonic()
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=cwd, env=env, shell=False, **kwargs
        )
    except (OSError, ValueError) as exc:
        raise ExecutionError("failed to start sandbox launcher") from exc

    stdout, stderr = bytearray(), bytearray()
    lock = threading.Lock()
    truncated = [False]
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, stdout, max_output_bytes, lock, truncated), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, stderr, max_output_bytes, lock, truncated), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        proc.wait(timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _terminate_tree(proc)
            proc.wait()
    finally:
        for thread in threads:
            thread.join(timeout=5)
        # If a descendant inherited a pipe, it may keep a reader alive. Kill the
        # process group on timeout; do not wait forever for a leaked descriptor.
    return ExecutionResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=bytes(stdout), stderr=bytes(stderr),
        duration_seconds=max(0.0, time.monotonic() - start),
        timed_out=timed_out, output_truncated=truncated[0],
    )
