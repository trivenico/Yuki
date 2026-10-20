"""Bounded JSON worker execution with timeout and streaming output limits."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time


class _BoundedPipeReader(threading.Thread):
    """Drain a pipe without retaining more than limit + 1 bytes."""

    def __init__(self, pipe, limit: int, overflow: threading.Event) -> None:
        super().__init__(daemon=True)
        self.pipe = pipe
        self.limit = limit
        self.overflow = overflow
        self.data = bytearray()
        self.read_error: OSError | None = None

    def run(self) -> None:
        try:
            while True:
                chunk = self.pipe.read(8192)
                if not chunk:
                    return
                remaining = self.limit + 1 - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining or len(self.data) > self.limit:
                    self.overflow.set()
                    # Continue draining until the parent terminates the process,
                    # avoiding a pipe-buffer deadlock during cleanup.
        except OSError as exc:
            self.read_error = exc
        finally:
            self.pipe.close()


class SubprocessExecutor:
    def __init__(self, *, timeout_seconds=5, max_output_bytes=65536):
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 60
        ):
            raise ValueError("invalid timeout")
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 1048576:
            raise ValueError("invalid output limit")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes

    def execute(self, command, payload, *, timeout_seconds=None, max_output_bytes=None):
        if (
            not isinstance(command, (list, tuple))
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise ValueError("command must be a non-empty argument list")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("invalid timeout")
        if max_output_bytes is not None and (
            type(max_output_bytes) is not int or max_output_bytes < 1
        ):
            raise ValueError("invalid output limit")
        timeout = min(
            self.timeout_seconds,
            float(timeout_seconds) if timeout_seconds is not None else self.timeout_seconds,
        )
        limit = min(
            self.max_output_bytes,
            max_output_bytes if max_output_bytes is not None else self.max_output_bytes,
        )
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
        if len(encoded) > 1048576:
            raise ValueError("worker input exceeded limit")

        kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(list(command), **kwargs)
        except OSError as exc:
            raise RuntimeError("worker could not be started") from exc

        overflow = threading.Event()
        stdout_reader = _BoundedPipeReader(process.stdout, limit, overflow)
        stderr_reader = _BoundedPipeReader(process.stderr, limit, overflow)
        stdout_reader.start()
        stderr_reader.start()

        def write_input() -> None:
            try:
                process.stdin.write(encoded)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                try:
                    process.stdin.close()
                except OSError:
                    pass

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        try:
            while True:
                if overflow.is_set():
                    self._terminate(process)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    self._terminate(process)
                    break
                # A worker can exit while a descendant keeps inherited pipes open.
                # Include pipe-reader completion in the deadline, not just leader exit.
                if (
                    process.poll() is not None
                    and not stdout_reader.is_alive()
                    and not stderr_reader.is_alive()
                ):
                    break
                time.sleep(min(0.01, remaining))
        finally:
            abnormal = timed_out or overflow.is_set()
            if abnormal:
                self._terminate(process)
                # The direct worker may exit on SIGTERM while a descendant ignores
                # it and keeps inherited pipes open. Kill the POSIX process group
                # after a short grace period, even if the group leader has exited.
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
                if os.name == "posix":
                    self._kill_process_group(process)
            elif process.poll() is None:
                self._terminate(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._kill(process)
                process.wait()
            stdout_reader.join(timeout=1)
            stderr_reader.join(timeout=1)
            writer.join(timeout=1)

        if timed_out:
            raise TimeoutError("worker timed out")
        if overflow.is_set() or len(stdout_reader.data) > limit or len(stderr_reader.data) > limit:
            raise ValueError("worker output exceeded limit")
        if stdout_reader.is_alive() or stderr_reader.is_alive():
            raise RuntimeError("worker output readers did not stop")
        if process.returncode != 0:
            raise RuntimeError("worker failed")
        try:
            result = json.loads(bytes(stdout_reader.data).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("worker returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("worker response must be an object")
        return result

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill_process_group(process: subprocess.Popen) -> None:
        if os.name != "posix":
            SubprocessExecutor._kill(process)
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
