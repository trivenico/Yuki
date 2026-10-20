"""Bounded JSON worker execution with a hard timeout and output limits."""

import json
import subprocess


class SubprocessExecutor:
    def __init__(self, *, timeout_seconds=5, max_output_bytes=65536):
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 60:
            raise ValueError("invalid timeout")
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 1048576:
            raise ValueError("invalid output limit")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def execute(self, command, payload, *, timeout_seconds=None, max_output_bytes=None):
        if (
            not isinstance(command, (list, tuple))
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise ValueError("command must be a non-empty argument list")
        timeout = min(self.timeout_seconds, timeout_seconds or self.timeout_seconds)
        limit = min(self.max_output_bytes, max_output_bytes or self.max_output_bytes)
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
        try:
            completed = subprocess.run(
                list(command),
                input=encoded,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                shell=False,
                close_fds=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("worker timed out") from exc
        if len(completed.stdout) > limit or len(completed.stderr) > limit:
            raise ValueError("worker output exceeded limit")
        if completed.returncode != 0:
            raise RuntimeError("worker failed")
        try:
            result = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("worker returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("worker response must be an object")
        return result
