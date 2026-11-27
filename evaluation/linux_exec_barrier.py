"""One-shot local readiness/release channel for a trusted Linux bootstrap.

This primitive provides message ordering only. The launcher must close the
parent endpoint in the child and the child endpoint in the parent immediately
after fork, keep the child blocked in trusted bootstrap code, and separately
verify pidfd identity, descriptor allowlist, cgroup placement, and artifact
binding. It is not a sandbox and does not itself stop candidate code.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import socket

_READY = b"YUKI_READY_V1"
_RELEASE = b"YUKI_RELEASE_V1"
_MAX_TIMEOUT_SECONDS = 3600.0


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite number of seconds")
    value = float(timeout)
    if not math.isfinite(value) or value <= 0 or value > _MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout must be greater than zero and at most one hour")
    return value


@dataclass(slots=True)
class ParentExecBarrier:
    """Parent endpoint; release is impossible until one valid READY packet."""

    _channel: socket.socket
    _ready: bool = False
    _released: bool = False
    _closed: bool = False

    def wait_ready(self, timeout: float) -> None:
        if self._closed or self._ready or self._released:
            raise RuntimeError("parent readiness transition is not available")
        self._channel.settimeout(_validate_timeout(timeout))
        try:
            message = self._channel.recv(64)
        except socket.timeout as exc:
            raise TimeoutError("child readiness handshake timed out") from exc
        if message != _READY:
            raise ConnectionError("child readiness channel closed or sent invalid data")
        self._ready = True

    def release(self) -> None:
        if self._closed or not self._ready or self._released:
            raise RuntimeError("exec release requires one completed readiness handshake")
        try:
            sent = self._channel.send(_RELEASE)
        except OSError as exc:
            raise ConnectionError("child release channel is unavailable") from exc
        if sent != len(_RELEASE):
            raise ConnectionError("exec release packet was not sent atomically")
        self._released = True

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._channel.close()


@dataclass(slots=True)
class ChildExecBarrier:
    """Trusted-bootstrap endpoint; candidate code must not inherit this channel."""

    _channel: socket.socket
    _reported: bool = False
    _released: bool = False
    _closed: bool = False

    def report_ready(self) -> None:
        if self._closed or self._reported or self._released:
            raise RuntimeError("child readiness transition is not available")
        try:
            sent = self._channel.send(_READY)
        except OSError as exc:
            raise ConnectionError("parent readiness channel is unavailable") from exc
        if sent != len(_READY):
            raise ConnectionError("readiness packet was not sent atomically")
        self._reported = True

    def wait_release(self, timeout: float) -> None:
        if self._closed or not self._reported or self._released:
            raise RuntimeError("child must report readiness before waiting for release")
        self._channel.settimeout(_validate_timeout(timeout))
        try:
            message = self._channel.recv(64)
        except socket.timeout as exc:
            raise TimeoutError("exec release handshake timed out") from exc
        if message != _RELEASE:
            raise ConnectionError("parent release channel closed or sent invalid data")
        self._released = True

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._channel.close()


def create_linux_exec_barrier() -> tuple[ParentExecBarrier, ChildExecBarrier]:
    """Create CLOEXEC, sequenced-packet endpoints for a forked trusted bootstrap."""
    if not hasattr(socket, "SOCK_SEQPACKET") or not hasattr(socket, "SOCK_CLOEXEC"):
        raise RuntimeError("Linux sequenced-packet CLOEXEC sockets are unavailable")
    kind = socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC
    parent, child = socket.socketpair(socket.AF_UNIX, kind)
    parent.set_inheritable(False)
    child.set_inheritable(False)
    return ParentExecBarrier(parent), ChildExecBarrier(child)
