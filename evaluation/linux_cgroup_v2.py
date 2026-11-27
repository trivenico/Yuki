"""Experimental descriptor-pinned cgroup-v2 attempt lifecycle.

The caller must supply an already authenticated, delegated cgroup-v2 mount-root
FD from the same cgroup namespace visible to the target process. This module
creates one child cgroup, applies the resource limits represented by
RunnerIsolationPolicy, reads them back, and can move a blocked PID into it.
It does not enable controllers, configure parent limits, apply namespaces,
seccomp, mounts, credentials, or prove production isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import os
import re
import secrets
import stat

from evaluation.cgroup_v2_fd import (
    PinnedCgroupDirectory,
    open_pinned_cgroup_directory,
    pin_cgroup_mount_root,
    pinned_cgroup_contains_pid,
    read_pinned_cgroup_control,
)
from evaluation.runner_isolation_policy import RunnerIsolationPolicy, verify_cgroup_v2_readback

_CONTROL_NAMES = ("cpu.max", "memory.max", "memory.swap.max", "pids.max")
_NAME = re.compile(r"^yuki-[a-f0-9]{24}$")
_MAX_WRITE = 128


def _write_control(directory: PinnedCgroupDirectory, filename: str, value: str) -> None:
    if filename not in _CONTROL_NAMES + ("cgroup.procs",):
        raise ValueError("cgroup control filename is not allowlisted")
    if type(value) is not str or not value or len(value) > _MAX_WRITE:
        raise ValueError("cgroup control value is invalid")
    if not value.isascii() or any(ch in value for ch in "\x00\r\n"):
        raise ValueError("cgroup control value contains invalid characters")
    directory_fd = directory.fileno()
    names = ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not getattr(os, name, 0) for name in names):
        raise OSError(errno.ENOTSUP, "required cgroup write protections are unavailable")
    flags = os.O_WRONLY
    for name in names:
        flags |= getattr(os, name)
    fd = os.open(filename, flags, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_dev != directory.device:
            raise ValueError("cgroup control is not a regular file on the pinned filesystem")
        written = os.write(fd, value.encode("ascii"))
        if written != len(value):
            raise OSError(errno.EIO, "cgroup control write was incomplete")
    finally:
        os.close(fd)


@dataclass(slots=True)
class LinuxCgroupV2Attempt:
    """Owned attempt cgroup and trusted delegated-root pin."""

    root: PinnedCgroupDirectory
    child: PinnedCgroupDirectory
    name: str
    policy_digest: str
    _removed: bool = False

    def apply_and_verify_policy(self, policy: RunnerIsolationPolicy) -> None:
        if self._removed or not isinstance(policy, RunnerIsolationPolicy):
            raise ValueError("active cgroup and valid policy are required")
        if not policy.verify() or policy.policy_digest != self.policy_digest:
            raise ValueError("cgroup policy does not match the created attempt")
        settings = policy.cgroup_v2_settings()
        for filename in _CONTROL_NAMES:
            _write_control(self.child, filename, settings[filename])
        observed = {
            filename: read_pinned_cgroup_control(self.child, filename)
            for filename in _CONTROL_NAMES
        }
        if not verify_cgroup_v2_readback(policy, observed):
            raise RuntimeError("kernel cgroup-v2 resource-limit readback mismatch")

    def place_pid(self, pid: int) -> None:
        if self._removed or type(pid) is not int or not 1 <= pid <= 2**31 - 1:
            raise ValueError("active cgroup and valid PID are required")
        _write_control(self.child, "cgroup.procs", str(pid))
        if not pinned_cgroup_contains_pid(self.child, pid):
            raise RuntimeError("kernel did not confirm target PID in attempt cgroup")

    def verify_pid(self, pid: int) -> bool:
        if self._removed:
            return False
        try:
            return pinned_cgroup_contains_pid(self.child, pid)
        except (OSError, ValueError, UnicodeError):
            return False

    def verify_empty(self) -> bool:
        if self._removed:
            return True
        try:
            return read_pinned_cgroup_control(self.child, "cgroup.procs") == ""
        except (OSError, ValueError, UnicodeError):
            return False

    def remove(self) -> None:
        if self._removed:
            return
        if not self.verify_empty():
            raise RuntimeError("refusing to remove a cgroup that is not empty")
        # Keep both pinned descriptors live until rmdir succeeds so a failed
        # removal remains inspectable and can be retried safely.
        os.rmdir(self.name, dir_fd=self.root.fileno())
        self.child.close()
        self._removed = True
        self.root.close()

    def close(self) -> None:
        if not self._removed:
            self.child.close()
            self.root.close()

    def __enter__(self) -> "LinuxCgroupV2Attempt":
        if self._removed:
            raise ValueError("cgroup attempt has already been removed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


@dataclass(slots=True)
class LinuxCgroupV2BlockedAttempt:
    """Blocked child plus its owned cgroup; release rechecks every local gate."""

    cgroup: LinuxCgroupV2Attempt
    child: object
    _finished: bool = False

    def verify_pre_release(self) -> bool:
        if self._finished:
            return False
        try:
            return (
                self.child.verify_identity()
                and self.child.verify_descriptor_allowlist()
                and self.child.verify_exec_barrier()
                and self.cgroup.verify_pid(self.child.pid)
            )
        except (OSError, ValueError, RuntimeError):
            return False

    def release(self) -> None:
        if not self.verify_pre_release():
            raise RuntimeError("blocked child failed final pre-release isolation checks")
        self.child.release()

    def wait(self, timeout_seconds: float) -> int:
        if self._finished:
            raise RuntimeError("blocked attempt is already finished")
        exit_code = self.child.wait(timeout_seconds)
        if not self.cgroup.verify_empty():
            raise RuntimeError("cgroup did not become empty after child exit")
        self.cgroup.remove()
        self.child.close()
        self._finished = True
        return exit_code

    def close(self) -> None:
        if self._finished:
            return
        self.child.abort_and_reap()
        if not self.cgroup.verify_empty():
            raise RuntimeError("refusing cleanup while attempt cgroup is not empty")
        self.cgroup.remove()
        self.child.close()
        self._finished = True

    def __enter__(self) -> "LinuxCgroupV2BlockedAttempt":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def fork_linux_blocked_child_in_cgroup(
    trusted_mount_root_fd: int,
    policy: RunnerIsolationPolicy,
    artifact_descriptor: int,
    *,
    readiness_timeout_seconds: float = 5.0,
) -> LinuxCgroupV2BlockedAttempt:
    """Create and verify a resource-limited cgroup before returning a blocked child.

    The child is not released by this function. Caller must still apply any
    additional namespace, mount, seccomp, credential, and artifact-to-child
    checks before calling attempt.release(). The supplied mount-root FD must
    be authenticated and from the target's cgroup namespace.
    """
    from evaluation.linux_blocked_child import fork_linux_blocked_child

    cgroup = create_linux_cgroup_v2_attempt(trusted_mount_root_fd, policy)
    child = None
    try:
        child = fork_linux_blocked_child(
            artifact_descriptor,
            readiness_timeout_seconds=readiness_timeout_seconds,
        )
        cgroup.place_pid(child.pid)
        attempt = LinuxCgroupV2BlockedAttempt(cgroup=cgroup, child=child)
        if not attempt.verify_pre_release():
            raise RuntimeError("child failed cgroup-bound pre-release verification")
        return attempt
    except BaseException:
        if child is not None:
            child.abort_and_reap()
            child.close()
        if cgroup.verify_empty():
            cgroup.remove()
        else:
            cgroup.close()
        raise


def create_linux_cgroup_v2_attempt(
    trusted_mount_root_fd: int,
    policy: RunnerIsolationPolicy,
) -> LinuxCgroupV2Attempt:
    """Create a unique attempt cgroup beneath a caller-trusted delegated root.

    Creation is fail-closed. The root FD's cgroup-v2 identity and namespace
    suitability remain caller responsibilities; this function only pins the
    supplied directory and enforces mount-boundary checks on descendants.
    """
    if not isinstance(policy, RunnerIsolationPolicy) or not policy.verify():
        raise ValueError("valid runner isolation policy is required")
    root = pin_cgroup_mount_root(trusted_mount_root_fd)
    name = "yuki-" + secrets.token_hex(12)
    try:
        os.mkdir(name, mode=0o700, dir_fd=root.fileno())
        try:
            child = open_pinned_cgroup_directory(root.fileno(), name)
        except BaseException:
            try:
                os.rmdir(name, dir_fd=root.fileno())
            except OSError:
                pass
            raise
        attempt = LinuxCgroupV2Attempt(
            root=root,
            child=child,
            name=name,
            policy_digest=policy.policy_digest,
        )
        try:
            attempt.apply_and_verify_policy(policy)
        except BaseException:
            try:
                attempt.remove()
            except BaseException:
                attempt.close()
            raise
        return attempt
    except BaseException:
        if root.descriptor >= 0:
            root.close()
        raise
