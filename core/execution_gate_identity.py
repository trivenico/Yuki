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

    def __call__(self, gate_pid: int) -> int:
        if type(gate_pid) is not int or gate_pid <= 0:
            raise ValueError("gate_pid must be a positive integer")
        pid_dir = self.proc_root / str(gate_pid)
        try:
            start_before = self._start_time(pid_dir)
            argv = (pid_dir / "cmdline").read_bytes().split(b"\x00")
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
            # The gate must not have changed credentials. The effective UID is
            # the credential observed by SO_PEERCRED; requiring all four to
            # match prevents hidden setuid/fsuid transitions.
            if len({real_uid, effective_uid, saved_uid, fs_uid}) != 1:
                raise ExecutionGateIdentityError("gate process has inconsistent UID credentials")
            start_after = self._start_time(pid_dir)
            if start_before != start_after:
                raise ExecutionGateIdentityError("host PID was reused during identity inspection")
            return effective_uid
        except ExecutionGateIdentityError:
            raise
        except (OSError, UnicodeError, StopIteration, ValueError) as exc:
            raise ExecutionGateIdentityError("could not safely resolve the host gate process identity") from exc


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
        uid = self.uid_resolver(host_pid)
        if type(uid) is not int or uid < 0:
            raise ExecutionGateIdentityError("host gate UID resolver returned an invalid UID")
        self.listener.bind_gate_identity(gate_pid=host_pid, gate_uid=uid)
        self._bound = True
