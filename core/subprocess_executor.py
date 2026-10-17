"""Bounded subprocess protocol for trusted worker executables.

This is process separation, not a complete OS sandbox. The executable and argv must
be configured by trusted application code, never derived from model/plugin input.
Use OS/container policy to restrict filesystem, network, and resource access.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
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
        try:
            completed = subprocess.run(
                list(argv), input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout_seconds, cwd=cwd, env=child_env or {},
                shell=False, check=False, close_fds=True,
            )
        except subprocess.TimeoutExpired:
            return WorkerResult(False, "worker_timeout")
        except (OSError, ValueError):
            return WorkerResult(False, "worker_start_failed")
        # Enforce output bounds after process completion; the process should also run
        # under an OS-level memory/output limit to protect against excessive output.
        if len(completed.stdout) > self.max_response_bytes or len(completed.stderr) > self.max_response_bytes:
            return WorkerResult(False, "worker_output_too_large")
        if completed.returncode != 0:
            return WorkerResult(False, "worker_failed")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
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
