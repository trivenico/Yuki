"""Explicitly approved workspace edits and allow-listed test execution.

This module is intentionally not a sandbox. It requires a trusted principal resolver,
a digest-bound approval callback, an explicit path allow-list, and explicit test targets.
It never accepts shell command strings or executes model-generated commands.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping, Sequence

from automation.authorized_read_tools import ToolPrincipal, ToolInputError, ToolAuthorizationError

_MAX_CHANGES = 8
_MAX_FILE_BYTES = 256 * 1024
_MAX_PATCH_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 64 * 1024
_MAX_TEST_TIMEOUT = 120
_SAFE_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_BLOCKED_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh", ".aws", ".azure", ".config"})
_SECRET_PART = re.compile(r"(^\.env($|\.)|secret|credential|password|token|private.?key|id_rsa|id_ed25519)", re.I)


class WorkspaceChangeError(RuntimeError):
    """An approved workspace change could not be safely applied."""


@dataclass(frozen=True)
class FileChange:
    path: str
    expected_sha256: str
    content: str


@dataclass(frozen=True)
class ChangeResult:
    change_id: str
    actor_id: str
    changed_paths: tuple[str, ...]
    content_digest: str


@dataclass(frozen=True)
class TestRunResult:
    target: str
    exit_code: int
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str


def _validate_principal(principal: ToolPrincipal, trusted: Callable[[ToolPrincipal], bool], permission: str) -> None:
    if not isinstance(principal, ToolPrincipal):
        raise ToolAuthorizationError("trusted authenticated principal required.")
    try:
        allowed = trusted(principal) is True
    except Exception:
        allowed = False
    if not allowed or not isinstance(principal.actor_id, str) or not principal.actor_id.strip():
        raise ToolAuthorizationError("principal validation failed closed.")
    if not isinstance(principal.permissions, frozenset) or permission not in principal.permissions:
        raise ToolAuthorizationError("required workspace permission is missing.")


def _safe_existing_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or len(relative) > 1000 or "\x00" in relative:
        raise ToolInputError("path must be bounded relative text.")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in {"", ".."} for part in candidate.parts):
        raise ToolInputError("absolute paths and parent traversal are not allowed.")
    if any(part in _BLOCKED_PARTS or _SECRET_PART.search(part) for part in candidate.parts):
        raise ToolInputError("path is excluded by workspace policy.")
    target = root / candidate
    # Reject symlinks at every existing path component; resolution alone would follow them.
    cursor = root
    for part in candidate.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ToolInputError("symlink paths are not allowed for workspace edits.")
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ToolInputError("path must resolve to an existing file inside the workspace.") from exc
    if not resolved.is_file() or resolved == root:
        raise ToolInputError("path must identify an existing regular file.")
    if resolved.stat().st_size > _MAX_FILE_BYTES:
        raise ToolInputError("existing file exceeds the edit size limit.")
    return resolved


class ApprovedWorkspaceChanges:
    """Apply digest-bound edits only after trusted authorization and explicit approval."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        trusted_principal: Callable[[ToolPrincipal], bool],
        approve_change: Callable[[str, str, str], bool],
        audit: Callable[[dict], None],
        allowed_paths: Sequence[str],
    ):
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("workspace must be an existing directory.")
        if not all(callable(fn) for fn in (trusted_principal, approve_change, audit)):
            raise TypeError("trusted principal, approval, and audit callbacks are required.")
        if not isinstance(allowed_paths, (tuple, list)) or not allowed_paths:
            raise ValueError("explicit allowed_paths are required.")
        self.root = root
        self.trusted_principal = trusted_principal
        self.approve_change = approve_change
        self.audit = audit
        self.allowed_paths = frozenset(allowed_paths)

    def _audit(self, actor: str, action: str, outcome: str, digest: str, paths: Sequence[str]) -> None:
        try:
            self.audit({"actor_id": actor[:200], "action": action, "outcome": outcome,
                        "content_digest": digest, "paths": list(paths)[:_MAX_CHANGES]})
        except Exception as exc:
            raise RuntimeError("audit unavailable; workspace operation denied.") from exc

    def apply(self, *, principal: ToolPrincipal, changes: Sequence[FileChange]) -> ChangeResult:
        _validate_principal(principal, self.trusted_principal, "workspace:write")
        if not isinstance(changes, (tuple, list)) or not 1 <= len(changes) <= _MAX_CHANGES:
            raise ToolInputError("one to eight file changes are required.")
        normalized = []
        seen = set()
        total = 0
        for change in changes:
            if not isinstance(change, FileChange):
                raise ToolInputError("each change must be a FileChange.")
            if change.path not in self.allowed_paths or change.path in seen:
                raise ToolAuthorizationError("path is not explicitly allowed or is duplicated.")
            seen.add(change.path)
            if not isinstance(change.expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", change.expected_sha256):
                raise ToolInputError("expected_sha256 must be a lowercase SHA-256 digest.")
            if not isinstance(change.content, str) or "\x00" in change.content:
                raise ToolInputError("file content must be text without NUL bytes.")
            encoded = change.content.encode("utf-8")
            total += len(encoded)
            if len(encoded) > _MAX_FILE_BYTES or total > _MAX_PATCH_BYTES:
                raise ToolInputError("proposed changes exceed the size limit.")
            target = _safe_existing_file(self.root, change.path)
            current = target.read_bytes()
            actual = hashlib.sha256(current).hexdigest()
            if actual != change.expected_sha256:
                raise WorkspaceChangeError("file changed since proposal; refresh and reapprove.")
            normalized.append((change.path, target, current, encoded))
        digest_input = [{"path": path, "expected_sha256": hashlib.sha256(old).hexdigest(),
                         "new_sha256": hashlib.sha256(new).hexdigest()} for path, _, old, new in normalized]
        digest = hashlib.sha256(json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        change_id = "chg_" + digest[:24]
        self._audit(principal.actor_id, "workspace.apply", "requested", digest, [x[0] for x in normalized])
        try:
            approved = self.approve_change(principal.actor_id, change_id, digest) is True
        except Exception:
            approved = False
        if not approved:
            self._audit(principal.actor_id, "workspace.apply", "denied", digest, [x[0] for x in normalized])
            raise ToolAuthorizationError("explicit approval for this exact change digest is required.")

        staged = []
        replaced = []
        try:
            for relative, target, old, new in normalized:
                fd, temp_name = tempfile.mkstemp(prefix=".yuki-stage-", dir=str(target.parent))
                with os.fdopen(fd, "wb") as handle:
                    handle.write(new)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged.append((relative, target, old, Path(temp_name)))
            # Recheck all source digests immediately before replacing any file.
            for relative, target, old, _ in staged:
                if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(old).digest():
                    raise WorkspaceChangeError("file changed during approval; no changes applied.")
            for relative, target, old, temp_path in staged:
                os.replace(temp_path, target)
                replaced.append((relative, target, old))
        except Exception as exc:
            rollback_errors = []
            for _, target, old in reversed(replaced):
                try:
                    fd, restore_name = tempfile.mkstemp(prefix=".yuki-restore-", dir=str(target.parent))
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(old)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(restore_name, target)
                except Exception as rollback_exc:
                    rollback_errors.append(type(rollback_exc).__name__)
            for _, _, _, temp_path in staged:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._audit(principal.actor_id, "workspace.apply", "failed", digest, [x[0] for x in normalized])
            if rollback_errors:
                raise WorkspaceChangeError("change failed and rollback needs operator attention.") from exc
            raise WorkspaceChangeError("change failed; replaced files were rolled back.") from exc
        self._audit(principal.actor_id, "workspace.apply", "succeeded", digest, [x[0] for x in normalized])
        return ChangeResult(change_id, principal.actor_id, tuple(x[0] for x in normalized), digest)


class ApprovedTestRunner:
    """Run only preconfigured unittest module targets; no shell or caller-supplied argv."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        trusted_principal: Callable[[ToolPrincipal], bool],
        approve_run: Callable[[str, str, str], bool],
        audit: Callable[[dict], None],
        allowed_test_modules: Sequence[str],
        timeout_seconds: int = 60,
    ):
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("workspace must be an existing directory.")
        if not all(callable(fn) for fn in (trusted_principal, approve_run, audit)):
            raise TypeError("trusted principal, approval, and audit callbacks are required.")
        if not isinstance(allowed_test_modules, (tuple, list)) or not allowed_test_modules:
            raise ValueError("explicit allowed_test_modules are required.")
        if any(not isinstance(m, str) or not _SAFE_MODULE.fullmatch(m) for m in allowed_test_modules):
            raise ValueError("test targets must be dotted Python module names.")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= _MAX_TEST_TIMEOUT:
            raise ValueError("timeout_seconds must be between 1 and 120.")
        self.root = root
        self.trusted_principal = trusted_principal
        self.approve_run = approve_run
        self.audit = audit
        self.allowed_test_modules = frozenset(allowed_test_modules)
        self.timeout_seconds = timeout_seconds

    def run(self, *, principal: ToolPrincipal, test_module: str) -> TestRunResult:
        _validate_principal(principal, self.trusted_principal, "tests:execute")
        if not isinstance(test_module, str) or test_module not in self.allowed_test_modules:
            raise ToolAuthorizationError("test target is not allow-listed.")
        target_path = self.root.joinpath(*test_module.split("."))
        module_file = target_path.with_suffix(".py")
        package_file = target_path / "__init__.py"
        if not module_file.is_file() and not package_file.is_file():
            raise ToolInputError("allow-listed test module is missing from the workspace.")
        source_digest = hashlib.sha256((module_file if module_file.is_file() else package_file).read_bytes()).hexdigest()
        run_digest = hashlib.sha256(f"{test_module}:{source_digest}:{sys.version_info.major}.{sys.version_info.minor}".encode()).hexdigest()
        run_id = "test_" + run_digest[:24]
        try:
            approved = self.approve_run(principal.actor_id, run_id, run_digest) is True
        except Exception:
            approved = False
        if not approved:
            self._record(principal.actor_id, "tests.run", "denied", run_digest, test_module)
            raise ToolAuthorizationError("explicit approval for this test target and source digest is required.")
        self._record(principal.actor_id, "tests.run", "started", run_digest, test_module)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(self.root),
               "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        start = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", test_module, "-v"],
                cwd=str(self.root), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.timeout_seconds,
                check=False, shell=False,
            )
            timed_out = False
            exit_code = result.returncode
            stdout = result.stdout[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stderr = result.stderr[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = (exc.stdout or b"")[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stderr = (exc.stderr or b"")[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        duration = round(time.monotonic() - start, 3)
        self._record(principal.actor_id, "tests.run", "timed_out" if timed_out else ("succeeded" if exit_code == 0 else "failed"), run_digest, test_module)
        return TestRunResult(test_module, exit_code, timed_out, duration, stdout, stderr)

    def _record(self, actor: str, action: str, outcome: str, digest: str, target: str) -> None:
        try:
            self.audit({"actor_id": actor[:200], "action": action, "outcome": outcome,
                        "content_digest": digest, "target": target[:200]})
        except Exception as exc:
            raise RuntimeError("audit unavailable; test operation denied.") from exc
