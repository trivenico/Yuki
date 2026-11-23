"""Resolve the host-visible identity of an image-owned, waiting execution gate.

This resolver is intentionally Linux-specific. It reads procfs from the host
namespace, requires a fixed gate argv[0], and checks process start time before
and after inspection to reduce PID-reuse races. Deployments must configure the
exact image-owned gate executable path and test UID mapping for their daemon.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


class ExecutionGateIdentityError(RuntimeError):
    """The inspected PID cannot be proven to be the expected waiting gate."""


class LinuxGateProcessIdentityResolver:
    """Return the host-visible effective UID for one exact gate process."""

    def __init__(
        self, *, expected_argv0: str = "/usr/local/bin/yuki-execution-gate",
        proc_root: str = "/proc",
    ) -> None:
        if type(expected_argv0) is not str or not expected_argv0.startswith("/") or "\x00" in expected_argv0:
            raise ValueError("expected_argv0 must be an absolute image-owned gate path")
        if type(proc_root) is not str or not os.path.isabs(proc_root):
            raise ValueError("proc_root must be absolute")
        self.expected_argv0 = expected_argv0
        self.proc_root = Path(proc_root)

    def _start_time(self, pid_dir: Path) -> str:
        raw = (pid_dir / "stat").read_text(encoding="ascii")
        close = raw.rfind(")")
        if close < 0:
            raise ExecutionGateIdentityError("proc stat record is malformed")
        fields = raw[close + 2:].split()
        # The suffix begins at field 3 (state); starttime is field 22.
        if len(fields) <= 19 or not fields[19].isdigit():
            raise ExecutionGateIdentityError("proc start time is unavailable")
        return fields[19]

    def resolve_identity(self, gate_pid: int) -> tuple[int, str]:
        if type(gate_pid) is not int or gate_pid <= 0:
            raise ValueError("gate_pid must be a positive integer")
        pid_dir = self.proc_root / str(gate_pid)
        try:
            start_before = self._start_time(pid_dir)
            argv = (pid_dir / "cmdline").read_bytes().split(b"\\x00")
            argv = [part.decode("utf-8", errors="strict") for part in argv if part]
            if not argv or argv[0] != self.expected_argv0:
                raise ExecutionGateIdentityError("host PID is not running the configured image-owned gate")
            status = (pid_dir / "status").read_text(encoding="ascii")
            uid_line = next((line for line in status.splitlines() if line.startswith("Uid:")), None)
            if uid_line is None:
                raise ExecutionGateIdentityError("host proc status has no UID credentials")
            values = uid_line.split()[1:]
            if len(values) != 4 or any(not re.fullmatch(r"[0-9]+", value) for value in values):
                raise ExecutionGateIdentityError("host proc UID credentials are malformed")
            real_uid, effective_uid, saved_uid, fs_uid = (int(value) for value in values)
            if len({real_uid, effective_uid, saved_uid, fs_uid}) != 1:
                raise ExecutionGateIdentityError("gate process has inconsistent UID credentials")
            start_after = self._start_time(pid_dir)
            if start_before != start_after:
                raise ExecutionGateIdentityError("host PID was reused during identity inspection")
            return effective_uid, start_before
        except ExecutionGateIdentityError:
            raise
        except (OSError, UnicodeError, StopIteration, ValueError) as exc:
            raise ExecutionGateIdentityError("could not safely resolve the host gate process identity") from exc

    def __call__(self, gate_pid: int) -> int:
        return self.resolve_identity(gate_pid)[0]



class ExecutionGateIdentityBinder:
    """Adapt Docker supervisor observations to a prepared one-shot listener."""

    def __init__(self, *, listener, uid_resolver) -> None:
        if not callable(getattr(listener, "bind_gate_identity", None)):
            raise TypeError("listener must expose bind_gate_identity")
        if not callable(uid_resolver):
            raise TypeError("uid_resolver must be callable")
        self.listener = listener
        self.uid_resolver = uid_resolver
        self._bound = False
        self.start_time: str | None = None

    def __call__(self, container_id: str, host_pid: int, inspected_container: dict) -> None:
        if self._bound:
            raise ExecutionGateIdentityError("gate identity binder is one-shot")
        if type(container_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise ExecutionGateIdentityError("daemon-assigned container ID is invalid")
        if not isinstance(inspected_container, dict) or inspected_container.get("Id") != container_id:
            raise ExecutionGateIdentityError("Docker inspect identity does not match the gate container")
        state = inspected_container.get("State")
        if not isinstance(state, dict) or state.get("Running") is not True or state.get("Pid") != host_pid:
            raise ExecutionGateIdentityError("Docker inspect does not confirm the same running gate PID")
        identity_resolver = getattr(self.uid_resolver, "resolve_identity", None)
        if callable(identity_resolver):
            uid, start_time = identity_resolver(host_pid)
            if type(start_time) is not str or not start_time.isdigit():
                raise ExecutionGateIdentityError("host gate start-time identity is invalid")
        else:
            uid = self.uid_resolver(host_pid)
            start_time = None
        if type(uid) is not int or uid < 0:
            raise ExecutionGateIdentityError("host gate UID resolver returned an invalid UID")
        self.listener.bind_gate_identity(gate_pid=host_pid, gate_uid=uid)
        self.start_time = start_time
        self._bound = True


class LinuxGateExecTransitionObserver:
    """Prove the gate PID has exec'd the exact authorized argv.

    This is a bounded host-procfs observation, not a claim that the task will
    complete successfully. A short-lived command may exit before it can be
    observed; in that case the caller must report an unconfirmed start.
    """

    def __init__(self, *, proc_root: str = "/proc", poll_interval: float = 0.02) -> None:
        if type(proc_root) is not str or not os.path.isabs(proc_root):
            raise ValueError("proc_root must be absolute")
        if type(poll_interval) not in (int, float) or isinstance(poll_interval, bool) or not 0.005 <= poll_interval <= 0.5:
            raise ValueError("poll_interval must be between 0.005 and 0.5 seconds")
        self.proc_root = Path(proc_root)
        self.poll_interval = float(poll_interval)

    @staticmethod
    def _read_start_time(pid_dir: Path) -> str:
        raw = (pid_dir / "stat").read_text(encoding="ascii")
        close = raw.rfind(")")
        if close < 0:
            raise ExecutionGateIdentityError("proc stat record is malformed")
        fields = raw[close + 2:].split()
        if len(fields) <= 19 or not fields[19].isdigit():
            raise ExecutionGateIdentityError("proc start time is unavailable")
        return fields[19]

    @staticmethod
    def _read_uid(pid_dir: Path) -> int:
        status = (pid_dir / "status").read_text(encoding="ascii")
        line = next((item for item in status.splitlines() if item.startswith("Uid:")), None)
        if line is None:
            raise ExecutionGateIdentityError("proc UID credentials are unavailable")
        values = line.split()[1:]
        if len(values) != 4 or any(not re.fullmatch(r"[0-9]+", item) for item in values):
            raise ExecutionGateIdentityError("proc UID credentials are malformed")
        parsed = [int(item) for item in values]
        if len(set(parsed)) != 1:
            raise ExecutionGateIdentityError("task process changed UID credentials")
        return parsed[1]

    def wait_for_exec(
        self, *, gate_pid: int, expected_uid: int, expected_argv: list[str] | tuple[str, ...],
        timeout_seconds: float = 2.0, expected_start_time: str | None = None,
    ) -> bool:
        if type(gate_pid) is not int or gate_pid <= 0:
            raise ValueError("gate_pid must be positive")
        if type(expected_uid) is not int or expected_uid < 0:
            raise ValueError("expected_uid must be non-negative")
        if type(expected_argv) not in (list, tuple) or not expected_argv or len(expected_argv) > 64:
            raise ValueError("expected_argv must be a bounded non-empty sequence")
        if any(type(arg) is not str or not arg or len(arg) > 4096 or "\\x00" in arg for arg in expected_argv):
            raise ValueError("expected_argv contains an invalid argument")
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool) or not 0.05 <= timeout_seconds <= 10:
            raise ValueError("timeout_seconds must be between 0.05 and 10 seconds")

        if expected_start_time is not None and (type(expected_start_time) is not str or not expected_start_time.isdigit()):
            raise ValueError("expected_start_time must be a procfs start-time token")
        pid_dir = self.proc_root / str(gate_pid)
        try:
            original_start = self._read_start_time(pid_dir)
            if expected_start_time is not None and original_start != expected_start_time:
                raise ExecutionGateIdentityError("gate PID was replaced after initial identity binding")
            if self._read_uid(pid_dir) != expected_uid:
                raise ExecutionGateIdentityError("gate UID changed before task exec")
        except (OSError, UnicodeError, StopIteration, ValueError) as exc:
            if isinstance(exc, ExecutionGateIdentityError):
                raise
            raise ExecutionGateIdentityError("could not establish initial gate process identity") from exc

        import time
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline:
            try:
                start_before = self._read_start_time(pid_dir)
                raw_argv = (pid_dir / "cmdline").read_bytes().split(b"\\x00")
                actual_argv = [part.decode("utf-8", errors="strict") for part in raw_argv if part]
                uid = self._read_uid(pid_dir)
                start_after = self._read_start_time(pid_dir)
            except (OSError, UnicodeError, StopIteration, ValueError) as exc:
                if isinstance(exc, ExecutionGateIdentityError):
                    raise
                raise ExecutionGateIdentityError("gate process exited or became unreadable before exec was observed") from exc
            if start_before != original_start or start_after != original_start:
                raise ExecutionGateIdentityError("gate PID was reused during exec observation")
            if uid != expected_uid:
                raise ExecutionGateIdentityError("task process UID differs from the authorized gate UID")
            if actual_argv == list(expected_argv):
                return True
            time.sleep(min(self.poll_interval, max(0.0, deadline - time.monotonic())))
        return False
