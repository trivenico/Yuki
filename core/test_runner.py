"""Bounded, explicit project-test runner for acceptance evidence."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

_MAX_OUTPUT_CHARS = 4000
_ALLOWED_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
)


def run_project_unittest_suite(
    workspace: str | Path, *, timeout_seconds: int = 120
) -> dict[str, Any]:
    """Run unittest discovery without a shell and return bounded factual evidence.

    Project tests execute project-controlled Python code, so the caller must obtain
    explicit user approval before invoking this function.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise ValueError("Test timeout must be an integer between 1 and 120 seconds.")
    root = Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Configured workspace must be a directory.")
    tests_dir = root / "tests"
    if tests_dir.is_symlink() or not tests_dir.is_dir():
        raise ValueError("A real tests directory must exist in the workspace.")
    if not any(path.is_file() and not path.is_symlink() and path.name.startswith("test") and path.suffix == ".py"
               for path in tests_dir.iterdir()):
        raise ValueError("No top-level Python test files were found in the workspace tests directory.")

    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    env = {key: os.environ[key] for key in _ALLOWED_ENV_KEYS if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command, cwd=root, env=env, shell=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds, check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return {
            "command": command,
            "exit_code": completed.returncode,
            "timed_out": False,
            "passed": completed.returncode == 0,
            "stdout": stdout[-_MAX_OUTPUT_CHARS:],
            "stderr": stderr[-_MAX_OUTPUT_CHARS:],
            "scope": "python_unittest_discovery_only",
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "command": command,
            "exit_code": None,
            "timed_out": True,
            "passed": False,
            "stdout": str(stdout)[-_MAX_OUTPUT_CHARS:],
            "stderr": str(stderr)[-_MAX_OUTPUT_CHARS:],
            "scope": "python_unittest_discovery_only",
        }
