"""Bounded, explicit project-test runner for acceptance evidence."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

_MAX_OUTPUT_CHARS = 4000
_ALLOWED_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
)


def run_project_unittest_suite(
    workspace: str | Path, project_path: str = ".", *, timeout_seconds: int = 120
) -> dict[str, Any]:
    """Run unittest discovery without a shell and return bounded factual evidence.

    Project tests execute project-controlled Python code, so the caller must obtain
    explicit user approval before invoking this function.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise ValueError("Test timeout must be an integer between 1 and 120 seconds.")
    if getattr(sys, "frozen", False):
        raise ValueError(
            "Project test execution from the packaged desktop runtime is not enabled yet; "
            "use Yuki's Python source-mode CLI until a dedicated isolated runner is packaged."
        )
    workspace_root = Path(workspace).expanduser().resolve(strict=True)
    if not workspace_root.is_dir():
        raise ValueError("Configured workspace must be a directory.")
    if not isinstance(project_path, str) or not project_path.strip():
        raise ValueError("Provide a workspace-relative project path.")
    supplied = Path(project_path)
    if supplied.is_absolute():
        raise ValueError("Absolute project paths are not accepted.")
    root = workspace_root / supplied
    try:
        resolved_root = root.resolve(strict=True)
        resolved_root.relative_to(workspace_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Project must exist inside the configured Yuki workspace.") from exc
    cursor = root
    while cursor != workspace_root:
        if cursor.is_symlink():
            raise ValueError("Symbolic links are not accepted in project paths.")
        parent = cursor.parent
        if parent == cursor:
            raise ValueError("Project must exist inside the configured Yuki workspace.")
        cursor = parent
    root = resolved_root
    if not root.is_dir():
        raise ValueError("Project path must be a directory.")
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
            "test_count": int(match.group(1)) if (match := re.search(r"Ran (\d+) tests?", stdout + "\n" + stderr)) else 0,
            "passed": completed.returncode == 0 and bool(match and int(match.group(1)) > 0),
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
