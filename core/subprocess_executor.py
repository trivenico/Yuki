"""Bounded subprocess executor with streaming output caps and timeout cleanup.

Commands must be trusted, pre-registered argv. This is not an OS sandbox.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from typing import Any, Sequence


class SubprocessExecutor:
    def __init__(self, timeout_seconds: float = 10, max_output_bytes: int = 65536,
                 max_input_bytes: int = 1_048_576):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 120:
            raise ValueError("invalid timeout")
        if type(max_output_bytes) is not int or not 128 <= max_output_bytes <= 1_048_576:
            raise ValueError("invalid output limit")
        if type(max_input_bytes) is not int or not 128 <= max_input_bytes <= 1_048_576:
            raise ValueError("invalid input limit")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes
        self.max_input_bytes = max_input_bytes

    @staticmethod
    def _terminate(proc: subprocess.Popen, *, process_group: bool) -> None:
        if proc.poll() is not None:
            return
        try:
            if process_group and os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass

    def execute(self, argv: Sequence[str], payload: Any, *, timeout_seconds=None, max_output_bytes=None):
        if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(x, str) or not x for x in argv):
            raise ValueError("argv must be a non-empty string list")
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be positive")
        timeout = min(float(timeout), self.timeout_seconds)
        limit = self.max_output_bytes if max_output_bytes is None else max_output_bytes
        if type(limit) is not int or limit < 1:
            raise ValueError("output limit must be a positive integer")
        limit = min(limit, self.max_output_bytes)
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON-serializable") from exc
        if len(encoded) > self.max_input_bytes:
            raise ValueError("skill subprocess input limit exceeded")

        popen_kwargs = {}
        process_group = os.name == "posix"
        if process_group:
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            proc = subprocess.Popen(
                list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, close_fds=True, bufsize=0, **popen_kwargs
            )
        except OSError as exc:
            raise RuntimeError("skill subprocess could not be started") from exc

        output = {"stdout": bytearray(), "stderr": bytearray()}
        exceeded = threading.Event()
        reader_errors = []

        def drain(stream, key):
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    if len(output[key]) + len(chunk) > limit:
                        remaining = max(0, limit - len(output[key]))
                        output[key].extend(chunk[:remaining])
                        exceeded.set()
                        self._terminate(proc, process_group=process_group)
                        break
                    output[key].extend(chunk)
            except (OSError, ValueError) as exc:
                reader_errors.append(exc)

        readers = [
            threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()

        def write_input():
            try:
                if proc.stdin:
                    proc.stdin.write(encoded)
                    proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        while proc.poll() is None:
            if exceeded.is_set():
                self._terminate(proc, process_group=process_group)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._terminate(proc, process_group=process_group)
                break
            time.sleep(0.01)
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._terminate(proc, process_group=process_group)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        for reader in readers:
            reader.join(timeout=1)
        writer.join(timeout=0.1)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass

        if timed_out:
            raise TimeoutError("skill subprocess timed out")
        if exceeded.is_set():
            raise ValueError("skill subprocess output limit exceeded")
        if reader_errors:
            raise RuntimeError("skill subprocess output could not be read safely")
        if proc.returncode != 0:
            raise RuntimeError("skill subprocess failed")
        try:
            data = json.loads(bytes(output["stdout"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("skill subprocess returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("skill subprocess result must be an object")
        return data
