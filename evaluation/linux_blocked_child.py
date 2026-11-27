"""Experimental Linux forked bootstrap lifecycle; deliberately executes no candidate.

This helper proves only that a single-threaded parent can fork a minimal child,
close inherited descriptors, obtain a pidfd identity, inspect the child's FD
snapshot, and release a one-shot gate. On release the child exits successfully;
there is no execve, candidate interpreter, cgroup, namespace, seccomp, or
credential transition here. Do not use this helper to execute untrusted code.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import stat
import threading

from evaluation.linux_exec_barrier import (
    ChildExecBarrier,
    ParentExecBarrier,
    create_linux_exec_barrier,
)
from evaluation.linux_process_identity import PinnedLinuxProcess

_MAX_FD_LIMIT = 1_048_576


def _close_unlisted_descriptors(keep: tuple[int, ...]) -> None:
    """Close every descriptor except the exact bounded keep-set."""
    import resource

    soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft_limit == resource.RLIM_INFINITY:
        soft_limit = _MAX_FD_LIMIT
    if soft_limit > _MAX_FD_LIMIT:
        raise RuntimeError("parent descriptor limit exceeds bootstrap safety bound")
    allowed = sorted(set(keep))
    if any(type(fd) is not int or fd < 0 or fd >= soft_limit for fd in allowed):
        raise ValueError("bootstrap keep-set contains an invalid descriptor")
    cursor = 0
    for fd in allowed:
        if cursor < fd:
            os.closerange(cursor, fd)
        cursor = fd + 1
    if cursor < soft_limit:
        os.closerange(cursor, soft_limit)


def _verify_sealed_memfd(descriptor: int) -> None:
    """Require all four Linux memfd seals before exposing bytes to a child."""
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("Linux memfd sealing support is unavailable") from exc
    names = ("F_SEAL_SEAL", "F_SEAL_SHRINK", "F_SEAL_GROW", "F_SEAL_WRITE")
    if not hasattr(fcntl, "F_GET_SEALS") or any(
        not hasattr(fcntl, name) for name in names
    ):
        raise RuntimeError("required memfd seals are unavailable")
    required = 0
    for name in names:
        required |= getattr(fcntl, name)
    try:
        actual = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
    except OSError as exc:
        raise ValueError("artifact must be an immutable sealed memfd") from exc
    if actual & required != required:
        raise ValueError("artifact memfd is missing required seals")


def _child_bootstrap(
    parent_channel_fd: int,
    child_barrier: ChildExecBarrier,
    artifact_fd: int,
    timeout_seconds: float,
) -> None:
    """Run only trusted bootstrap protocol, then exit without executing bytes."""
    try:
        os.close(parent_channel_fd)
        _close_unlisted_descriptors(
            (0, 1, 2, artifact_fd, child_barrier._channel.fileno())
        )
        child_barrier.report_ready()
        child_barrier.wait_release(timeout_seconds)
        child_barrier.close()
        os._exit(0)
    except BaseException:
        try:
            child_barrier.close()
        finally:
            os._exit(70)


@dataclass(slots=True)
class LinuxBlockedChild:
    """Parent-owned handles for a blocked, non-executing child process."""

    pid: int
    process: PinnedLinuxProcess
    barrier: ParentExecBarrier
    artifact_fd: int
    readiness_timeout_seconds: float
    _released: bool = False
    _reaped: bool = False

    def verify_identity(self) -> bool:
        return self.process.verify()

    def verify_descriptor_allowlist(self) -> bool:
        expected = tuple(sorted((0, 1, 2, self.artifact_fd,
                                 self.barrier._channel.fileno())))
        return self.process.verify_descriptor_allowlist(expected)

    def verify_exec_barrier(self) -> bool:
        return self.process.verify() and self.barrier._ready and not self._released

    def release(self) -> None:
        if self._reaped or self._released:
            raise RuntimeError("blocked child cannot be released in its current state")
        self.barrier.release()
        self._released = True
        # The parent must not retain a usable control endpoint after release.
        self.barrier.close()

    def wait(self, timeout_seconds: float) -> int:
        if not self._released or self._reaped:
            raise RuntimeError("child must be released before it can be reaped")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ) or not 0 < timeout_seconds <= 3600:
            raise ValueError("wait timeout must be between zero and one hour")
        waited, status = os.waitpid(self.pid, os.WNOHANG)
        if waited == 0:
            import time
            deadline = time.monotonic() + timeout_seconds
            while waited == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
                waited, status = os.waitpid(self.pid, os.WNOHANG)
        if waited == 0:
            raise TimeoutError("blocked child did not exit within bounded wait")
        self._reaped = True
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
        raise RuntimeError("child reached an unexpected wait state")

    def abort_and_reap(self) -> None:
        if self._reaped:
            return
        try:
            os.kill(self.pid, 9)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        self._reaped = True

    def close(self) -> None:
        if not self._reaped:
            self.abort_and_reap()
        self.barrier.close()
        self.process.close()

    def __enter__(self) -> "LinuxBlockedChild":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def fork_linux_blocked_child(
    artifact_descriptor: int,
    *,
    readiness_timeout_seconds: float = 5.0,
) -> LinuxBlockedChild:
    """Fork a minimal child that remains blocked until explicit parent release.

    Requires a single-threaded parent because forking a multithreaded Python
    process can inherit locked runtime state. Artifact must be a regular file
    descriptor numbered above stderr. This helper does not enforce cgroups or
    other isolation policy and must not be treated as a production sandbox.
    """
    if os.name != "posix" or not hasattr(os, "fork") or not hasattr(os, "pidfd_open"):
        raise RuntimeError("Linux fork and pidfd support are required")
    if threading.active_count() != 1:
        raise RuntimeError("blocked-child fork requires a single-threaded parent")
    if type(artifact_descriptor) is not int or artifact_descriptor <= 2:
        raise ValueError("artifact descriptor must be an integer above stderr")
    if not stat.S_ISREG(os.fstat(artifact_descriptor).st_mode):
        raise ValueError("artifact descriptor must refer to a regular file")
    _verify_sealed_memfd(artifact_descriptor)
    if isinstance(readiness_timeout_seconds, bool) or not isinstance(
        readiness_timeout_seconds, (int, float)
    ) or not 0 < readiness_timeout_seconds <= 60:
        raise ValueError("readiness timeout must be between zero and 60 seconds")

    parent_barrier, child_barrier = create_linux_exec_barrier()
    pid = -1
    process = None
    try:
        pid = os.fork()
        if pid == 0:
            _child_bootstrap(
                parent_barrier._channel.fileno(),
                child_barrier,
                artifact_descriptor,
                float(readiness_timeout_seconds),
            )
            os._exit(70)
        child_barrier.close()
        process = PinnedLinuxProcess.open(pid)
        parent_barrier.wait_ready(float(readiness_timeout_seconds))
        handle = LinuxBlockedChild(
            pid=pid,
            process=process,
            barrier=parent_barrier,
            artifact_fd=artifact_descriptor,
            readiness_timeout_seconds=float(readiness_timeout_seconds),
        )
        if not handle.verify_identity() or not handle.verify_descriptor_allowlist():
            raise RuntimeError("blocked child identity or descriptor verification failed")
        if not handle.verify_exec_barrier():
            raise RuntimeError("blocked child readiness barrier verification failed")
        return handle
    except BaseException:
        parent_barrier.close()
        child_barrier.close()
        if process is not None:
            process.close()
        if pid > 0:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        raise
