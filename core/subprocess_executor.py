"""Bounded subprocess executor. Commands are registered as argv; shell is never used."""
import json
import subprocess

class SubprocessExecutor:
    def __init__(self, timeout_seconds=10, max_output_bytes=65536):
        if type(timeout_seconds) not in (int, float) or not 0.1 <= timeout_seconds <= 120: raise ValueError("invalid timeout")
        if type(max_output_bytes) is not int or not 128 <= max_output_bytes <= 1_048_576: raise ValueError("invalid output limit")
        self.timeout_seconds, self.max_output_bytes = float(timeout_seconds), max_output_bytes

    def execute(self, argv, payload, *, timeout_seconds=None, max_output_bytes=None):
        if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(x, str) or not x for x in argv): raise ValueError("argv must be a non-empty string list")
        timeout = min(float(timeout_seconds or self.timeout_seconds), self.timeout_seconds)
        limit = min(int(max_output_bytes or self.max_output_bytes), self.max_output_bytes)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            result = subprocess.run(list(argv), input=encoded, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, check=False, shell=False, close_fds=True)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("skill subprocess timed out") from exc
        if len(result.stdout) > limit or len(result.stderr) > limit: raise ValueError("skill subprocess output limit exceeded")
        if result.returncode != 0: raise RuntimeError("skill subprocess failed")
        try: data = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise ValueError("skill subprocess returned invalid JSON") from exc
        if not isinstance(data, dict): raise ValueError("skill subprocess result must be an object")
        return data
