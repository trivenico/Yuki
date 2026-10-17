"""Bounded subprocess protocol for trusted worker executables.

This is process separation, not a complete OS sandbox. The executable and argv must
be configured by trusted application code, never derived from model/plugin input.
Use OS/container policy to restrict filesystem, network, and resource access.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import signal
import subprocess
import threading
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class WorkerResult:
    ok: bool
    code: str
    result: Any = None


class SubprocessExecutor:
    def __init__(self, *, timeout_seconds: float = 10.0, max_request_bytes: int = 64 * 1024,
                 max_response_bytes: int = 64 * 1024, allowed_env: Sequence[str] = ()):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 0.1 and 300.")
        for name, value in (("max_request_bytes", max_request_bytes), ("max_response_bytes", max_response_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1024 * 1024:
                raise ValueError(f"{name} must be between 1 and 1 MiB.")
        if not isinstance(allowed_env, (tuple, list)) or any(not isinstance(k, str) or not k.isidentifier() for k in allowed_env):
            raise ValueError("allowed_env must contain environment variable names.")
        self.timeout_seconds = float(timeout_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.allowed_env = tuple(dict.fromkeys(allowed_env))

    def execute(self, argv: Sequence[str], request: Mapping[str, Any], *,
                cwd: str | None = None, env: Mapping[str, str] | None = None) -> WorkerResult:
        if not isinstance(argv, (tuple, list)) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            return WorkerResult(False, "invalid_argv")
        # Never use shell=True. Callers must provide a fixed, trusted executable and args.
        try:
            payload = json.dumps(dict(request), ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            return WorkerResult(False, "invalid_request")
        if len(payload) > self.max_request_bytes:
            return WorkerResult(False, "request_too_large")
        child_env = {key: os.environ[key] for key in self.allowed_env if key in os.environ}
        if env:
            for key, value in env.items():
                if key not in self.allowed_env or not isinstance(value, str):
                    return WorkerResult(False, "environment_not_allowed")
                child_env[key] = value
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=cwd, env=child_env or {}, shell=False, close_fds=True,
                start_new_session=(os.name == "posix"), creationflags=creationflags,
            )
        except (OSError, ValueError):
            return WorkerResult(False, "worker_start_failed")

        def kill_worker_tree() -> None:
            """Best-effort termination of the worker and descendants on supported OSes."""
            if process.poll() is not None:
                return
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=2, check=False, shell=False,
                    )
                else:
                    process.kill()
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass

        captured: dict[str, bytes] = {"stdout": b"", "stderr": b""}
        exceeded = threading.Event()

        def drain(name: str, stream) -> None:
            chunks: list[bytes] = []
            total = 0
            try:
                while True:
                    chunk = stream.read(min(8192, self.max_response_bytes + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        exceeded.set()
                        kill_worker_tree()
                        break
                    chunks.append(chunk)
                captured[name] = b"".join(chunks)
            finally:
                stream.close()

        def send_input() -> None:
            try:
                assert process.stdin is not None
                process.stdin.write(payload)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
        ]
        writer = threading.Thread(target=send_input, daemon=True)
        for thread in readers:
            thread.start()
        writer.start()
        try:
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            kill_worker_tree()
            process.wait()
            for thread in readers:
                thread.join(timeout=1)
            writer.join(timeout=1)
            return WorkerResult(False, "worker_timeout")
        for thread in readers:
            thread.join(timeout=1)
        writer.join(timeout=1)
        if exceeded.is_set():
            return WorkerResult(False, "worker_output_too_large")
        if any(thread.is_alive() for thread in readers) or writer.is_alive():
            kill_worker_tree()
            return WorkerResult(False, "worker_io_failed")
        if process.returncode is None:
            return WorkerResult(False, "worker_failed")
        if process.returncode != 0:
            return WorkerResult(False, "worker_failed")
        try:
            response = json.loads(captured["stdout"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return WorkerResult(False, "invalid_worker_response")
        if not isinstance(response, dict) or set(response) != {"ok", "result"} or type(response["ok"]) is not bool:
            return WorkerResult(False, "invalid_worker_response")
        if not response["ok"]:
            return WorkerResult(False, "worker_rejected")
        try:
            result_bytes = json.dumps(response["result"], ensure_ascii=False, allow_nan=False,
                                      separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            return WorkerResult(False, "invalid_worker_result")
        if len(result_bytes) > self.max_response_bytes:
            return WorkerResult(False, "worker_output_too_large")
        return WorkerResult(True, "ok", response["result"])
