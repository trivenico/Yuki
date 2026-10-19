"""Bounded subprocess execution helper."""
import subprocess
import sys


class SubprocessExecutor:
    def __init__(self, timeout_seconds: int = 3, max_output_bytes: int = 1024 * 1024):
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def run(self, cmd, input_data=None):
        proc = subprocess.run(
            cmd,
            input=input_data,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
        )
        return proc.stdout, proc.stderr, proc.returncode
