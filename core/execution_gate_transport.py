"""Private one-shot Unix-socket transport for execution-gate authorization.

The listener is host-owned and must be created before the container starts. The
container may mount only this socket into the gate's private runtime path; never
mount the containing directory or this endpoint into a task workspace.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import stat
import struct
import time
from pathlib import Path

_MAX_FRAME_BYTES = 40_000
_ACK = b"\x06"


class ExecutionGateTransportError(RuntimeError):
    """Private gate authorization transport failed closed."""


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise ExecutionGateTransportError("gate channel closed during a frame")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_gate_authorization(connection: socket.socket, *, expected_host_uid: int) -> tuple[str, bytes]:
    """Read one bounded frame from a connected host listener."""
    if not isinstance(connection, socket.socket):
        raise TypeError("connection must be a socket")
    if type(expected_host_uid) is not int or expected_host_uid < 0:
        raise ValueError("expected_host_uid must be a non-negative integer")
    if not hasattr(socket, "SO_PEERCRED"):
        raise ExecutionGateTransportError("peer credential verification is unavailable")
    try:
        _pid, peer_uid, _gid = struct.unpack("3i", connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"),
        ))
        if peer_uid != expected_host_uid:
            raise ExecutionGateTransportError("gate channel host UID does not match")
        header = _recv_exact(connection, 4)
        length = struct.unpack("!I", header)[0]
        if not 1 <= length <= _MAX_FRAME_BYTES:
            raise ExecutionGateTransportError("gate channel frame length is invalid")
        raw = _recv_exact(connection, length)
        def reject_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate transport field")
                result[key] = value
            return result

        payload = json.loads(raw.decode("ascii"), object_pairs_hook=reject_duplicate_keys)
        if type(payload) is not dict or set(payload) != {"protocol", "token", "key"}:
            raise ExecutionGateTransportError("gate channel fields are not exact")
        if payload["protocol"] != "yuki.execution-gate.transport.v1":
            raise ExecutionGateTransportError("gate channel protocol is unsupported")
        token = payload["token"]
        key_text = payload["key"]
        if type(token) is not str or not 1 <= len(token) <= 32_768:
            raise ExecutionGateTransportError("gate authorization token is invalid")
        try:
            token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ExecutionGateTransportError("gate authorization token must be ASCII") from exc
        if type(key_text) is not str or not 43 <= len(key_text) <= 172:
            raise ExecutionGateTransportError("gate authorization key is invalid")
        key = base64.b64decode(key_text, validate=True)
        if not 32 <= len(key) <= 128 or base64.b64encode(key).decode("ascii") != key_text:
            raise ExecutionGateTransportError("gate authorization key encoding is invalid")
        connection.sendall(_ACK)
        return token, key
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, struct.error) as exc:
        if isinstance(exc, ExecutionGateTransportError):
            raise
        raise ExecutionGateTransportError("gate channel frame is malformed or unavailable") from exc


class ExecutionGateAuthorizationListener:
    """Single-connection listener bound to an expected host PID and UID."""

    def __init__(
        self, *, socket_path: str, expected_gate_pid: int,
        expected_gate_uid: int, timeout_seconds: float = 10.0,
    ) -> None:
        if type(socket_path) is not str or not os.path.isabs(socket_path):
            raise ValueError("socket_path must be absolute")
        if len(os.fsencode(socket_path)) >= 100:
            raise ValueError("socket_path exceeds the portable Unix socket path limit")
        if type(expected_gate_pid) is not int or expected_gate_pid <= 0:
            raise ValueError("expected_gate_pid must be positive")
        if type(expected_gate_uid) is not int or expected_gate_uid < 0:
            raise ValueError("expected_gate_uid must be non-negative")
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        path = Path(socket_path)
        parent = path.parent
        if (parent.is_symlink() or not parent.is_dir()
                or os.path.realpath(str(parent)) != str(parent)):
            raise ValueError("gate socket parent must be an existing non-symlink directory")
        parent_stat = parent.stat(follow_symlinks=False)
        if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
            raise ValueError("gate socket parent must be service-owned and private (0700 or stricter)")
        if path.exists() or path.is_symlink():
            raise ValueError("gate socket path must not already exist")
        self.socket_path = str(path)
        self.expected_gate_pid = expected_gate_pid
        self.expected_gate_uid = expected_gate_uid
        self.timeout_seconds = float(timeout_seconds)
        self._listener: socket.socket | None = None
        self._consumed = False

    def open(self) -> None:
        if self._listener is not None or self._consumed:
            raise ExecutionGateTransportError("gate listener cannot be reopened")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(self.socket_path)
            os.chmod(self.socket_path, 0o600, follow_symlinks=False)
            listener.listen(1)
            listener.settimeout(self.timeout_seconds)
            self._listener = listener
        except Exception:
            listener.close()
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
            raise

    def deliver(self, *, token: str, key: bytes) -> None:
        """Accept exactly the expected gate process and deliver one authorization."""
        if self._listener is None or self._consumed:
            raise ExecutionGateTransportError("gate listener is not open or was already consumed")
        if type(token) is not str or not 1 <= len(token.encode("ascii", errors="ignore")) <= 32_768:
            raise ValueError("token is missing or oversized")
        if type(key) is not bytes or not 32 <= len(key) <= 128:
            raise ValueError("key must contain between 32 and 128 bytes")
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExecutionGateTransportError("timed out waiting for the image-owned gate")
                self._listener.settimeout(remaining)
                connection, _ = self._listener.accept()
                with connection:
                    if not hasattr(socket, "SO_PEERCRED"):
                        raise ExecutionGateTransportError("peer credential verification is unavailable")
                    pid, uid, _gid = struct.unpack("3i", connection.getsockopt(
                        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"),
                    ))
                    if pid != self.expected_gate_pid or uid != self.expected_gate_uid:
                        continue
                    payload = json.dumps({
                        "protocol": "yuki.execution-gate.transport.v1",
                        "token": token,
                        "key": base64.b64encode(key).decode("ascii"),
                    }, sort_keys=True, separators=(",", ":")).encode("ascii")
                    if len(payload) > _MAX_FRAME_BYTES:
                        raise ExecutionGateTransportError("authorization frame exceeds the configured limit")
                    connection.settimeout(min(2.0, self.timeout_seconds))
                    connection.sendall(struct.pack("!I", len(payload)) + payload)
                    if _recv_exact(connection, 1) != _ACK:
                        raise ExecutionGateTransportError("gate did not acknowledge authorization delivery")
                    self._consumed = True
                    return
        except (OSError, TimeoutError, struct.error) as exc:
            raise ExecutionGateTransportError("gate authorization delivery failed") from exc
        finally:
            self.close()

    def close(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
