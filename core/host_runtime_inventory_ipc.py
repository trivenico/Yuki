"""Authenticated Unix-domain IPC for host runtime inventory.

The client is callable by AuthenticatedRuntimeInventoryProvider. The agent
handler is intended to be hosted by an operator-managed service bound to one
Unix socket. Neither side accepts commands, paths, URLs, or task-controlled
code. HMAC response verification remains in the inventory provider.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import stat
import struct
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from core.host_runtime_inventory import RuntimeInventoryError
from core.authenticated_runtime_inventory import sign_runtime_inventory_envelope

_MAX_MESSAGE_BYTES = 32_768
_HEADER = struct.Struct("!I")
_REQUEST_FIELDS = {"operation", "task_id", "worker_id", "container_id", "image_ref", "spec_id"}
_RESPONSE_FIELDS = {
    "source_id", "task_id", "worker_id", "container_id", "image_ref", "spec_id",
    "issued_at", "expires_at", "nonce", "runtime_versions",
    "installed_dependencies", "signature",
}


def load_private_hmac_key(path: str, *, expected_uid: int | None = None) -> bytes:
    """Load an operator-provisioned key from a non-symlink regular 0600 file."""
    if type(path) is not str or not os.path.isabs(path):
        raise ValueError("HMAC key path must be an absolute operator-configured path.")
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("HMAC key must be a regular non-symlink file.")
    if info.st_mode & 0o077:
        raise ValueError("HMAC key file must not be accessible by group or others.")
    if expected_uid is not None and info.st_uid != expected_uid:
        raise ValueError("HMAC key file owner does not match the configured service UID.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_ino != info.st_ino or opened.st_dev != info.st_dev:
            raise ValueError("HMAC key file changed while opening.")
        if opened.st_mode & 0o077:
            raise ValueError("HMAC key file permissions are too broad.")
        key = os.read(fd, 4097).rstrip(bytes((13, 10)))
    finally:
        os.close(fd)
    if len(key) < 32 or len(key) > 4096:
        raise ValueError("HMAC key must contain between 32 and 4096 bytes.")
    return key


def _encode_message(value: Mapping[str, Any]) -> bytes:
    try:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeInventoryError("host-agent IPC message is not canonical JSON.") from exc
    if not body or len(body) > _MAX_MESSAGE_BYTES:
        raise RuntimeInventoryError("host-agent IPC message exceeds its byte ceiling.")
    return _HEADER.pack(len(body)) + body


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise RuntimeInventoryError("host-agent IPC peer closed an incomplete message.")
        chunks.extend(chunk)
    return bytes(chunks)


def _receive_message(sock: socket.socket) -> dict[str, Any]:
    length = _HEADER.unpack(_read_exact(sock, _HEADER.size))[0]
    if length < 2 or length > _MAX_MESSAGE_BYTES:
        raise RuntimeInventoryError("host-agent IPC frame length is invalid.")
    try:
        value = json.loads(_read_exact(sock, length).decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeInventoryError("host-agent IPC frame is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeInventoryError("host-agent IPC message must be an object.")
    return value


def _peer_uid(sock: socket.socket) -> int:
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeInventoryError("Unix peer credentials are unavailable on this platform.")
    credentials = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", credentials)
    return uid


class UnixSocketRuntimeInventoryProbe:
    """Fixed-endpoint callable that authenticates the local agent's peer UID."""

    def __init__(
        self, *, socket_path: str, expected_agent_uid: int,
        timeout_seconds: float = 2.0,
    ) -> None:
        if type(socket_path) is not str or not os.path.isabs(socket_path):
            raise ValueError("host-agent socket path must be absolute.")
        if type(expected_agent_uid) is not int or expected_agent_uid < 0:
            raise ValueError("expected_agent_uid must be a non-negative integer.")
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 10:
            raise ValueError("timeout_seconds must be between 0.1 and 10.")
        try:
            endpoint = os.stat(socket_path, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("host-agent socket must exist.") from exc
        allowed_gids = set(os.getgroups()) | {os.getegid()}
        group_writable = bool(endpoint.st_mode & 0o020)
        if (not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != expected_agent_uid
                or endpoint.st_mode & 0o002 or (group_writable and endpoint.st_gid not in allowed_gids)
                or os.path.islink(socket_path)):
            raise ValueError("host-agent socket must be owned by the configured agent UID and writable only by an authorized local group.")
        self.socket_path = socket_path
        self.expected_agent_uid = expected_agent_uid
        self.timeout_seconds = float(timeout_seconds)

    def __call__(self, task_id: str, worker_id: str, container_id: str, image_ref: str, spec_id: str) -> Mapping[str, Any]:
        values = (task_id, worker_id, container_id, image_ref, spec_id)
        if any(type(value) is not str or not value or len(value) > 512 for value in values):
            raise RuntimeInventoryError("host-agent IPC execution identity is invalid.")
        request = {
            "operation": "runtime_inventory.v1",
            "task_id": task_id, "worker_id": worker_id,
            "container_id": container_id, "image_ref": image_ref, "spec_id": spec_id,
        }
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(self.timeout_seconds)
        try:
            client.connect(self.socket_path)
            if _peer_uid(client) != self.expected_agent_uid:
                raise RuntimeInventoryError("host-agent IPC peer UID does not match the configured agent.")
            client.sendall(_encode_message(request))
            response = _receive_message(client)
        except (OSError, TimeoutError) as exc:
            raise RuntimeInventoryError("authenticated host-agent IPC request failed.") from exc
        finally:
            client.close()
        if set(response) != _RESPONSE_FIELDS:
            raise RuntimeInventoryError("host-agent IPC response has an invalid schema.")
        return response


class HostRuntimeInventoryAgent:
    """Validate fixed inventory requests and return a signed bounded envelope.

    inventory_collector must query trusted host/image metadata only. It receives
    identity fields, never a command or filesystem path from the task.
    """

    def __init__(
        self, *, source_id: str, hmac_key: bytes,
        inventory_collector: Callable[[str, str, str, str, str], Mapping[str, Mapping[str, str]]],
        expected_client_uid: int, clock: Callable[[], float] = time.time,
        validity_seconds: int = 30,
    ) -> None:
        if type(hmac_key) is not bytes or len(hmac_key) < 32:
            raise ValueError("host-agent HMAC key must contain at least 256 bits.")
        if type(source_id) is not str or not source_id or len(source_id) > 128:
            raise ValueError("host-agent source ID is invalid.")
        if not callable(inventory_collector) or not callable(clock):
            raise TypeError("trusted inventory collector and clock are required.")
        if type(expected_client_uid) is not int or expected_client_uid < 0:
            raise ValueError("expected_client_uid must be a non-negative integer.")
        if type(validity_seconds) is not int or not 1 <= validity_seconds <= 60:
            raise ValueError("validity_seconds must be between 1 and 60.")
        self.source_id = source_id
        self._key = bytes(hmac_key)
        self._collector = inventory_collector
        self.expected_client_uid = expected_client_uid
        self._clock = clock
        self.validity_seconds = validity_seconds

    def handle_connection(self, connection: socket.socket) -> None:
        """Handle one accepted connection; the service owns bind/listen lifecycle."""
        connection.settimeout(2.0)
        if _peer_uid(connection) != self.expected_client_uid:
            raise RuntimeInventoryError("host-agent IPC client UID is not authorized.")
        request = _receive_message(connection)
        if set(request) != _REQUEST_FIELDS or request.get("operation") != "runtime_inventory.v1":
            raise RuntimeInventoryError("host-agent IPC request schema or operation is invalid.")
        task_id, worker_id, container_id, image_ref, spec_id = (
            request[key] for key in ("task_id", "worker_id", "container_id", "image_ref", "spec_id")
        )
        if any(type(value) is not str or not value or len(value) > 512 for value in (task_id, worker_id, container_id, image_ref, spec_id)):
            raise RuntimeInventoryError("host-agent IPC request identity is invalid.")
        inventory = self._collector(task_id, worker_id, container_id, image_ref, spec_id)
        if not isinstance(inventory, Mapping) or set(inventory) != {"runtime_versions", "installed_dependencies"}:
            raise RuntimeInventoryError("host inventory collector returned an invalid schema.")
        issued_at = self._clock()
        if type(issued_at) not in (int, float) or isinstance(issued_at, bool) or issued_at <= 0:
            raise RuntimeInventoryError("host-agent clock is invalid.")
        unsigned = {
            "source_id": self.source_id, "task_id": task_id, "worker_id": worker_id,
            "container_id": container_id, "image_ref": image_ref, "spec_id": spec_id,
            "issued_at": int(issued_at), "expires_at": int(issued_at) + self.validity_seconds,
            "nonce": secrets.token_urlsafe(24),
            "runtime_versions": inventory["runtime_versions"],
            "installed_dependencies": inventory["installed_dependencies"],
        }
        response = {**unsigned, "signature": sign_runtime_inventory_envelope(unsigned, self._key)}
        connection.sendall(_encode_message(response))

def build_authenticated_runtime_inventory_provider(
    *,
    socket_path: str,
    expected_agent_uid: int,
    hmac_key: bytes,
    source_id: str,
    nonce_database_path: str,
    timeout_seconds: float = 2.0,
):
    """Compose IPC transport, signed-envelope verifier and durable replay store.

    Pass the returned callable directly as LinuxHostRuntimeEvidenceBuilder's
    runtime_inventory_provider. The nonce database must be on protected,
    durable storage shared by every Yuki process consuming this source.
    """
    from core.authenticated_runtime_inventory import (
        AuthenticatedRuntimeInventoryProvider,
        SQLiteRuntimeInventoryNonceStore,
    )

    probe = UnixSocketRuntimeInventoryProbe(
        socket_path=socket_path,
        expected_agent_uid=expected_agent_uid,
        timeout_seconds=timeout_seconds,
    )
    nonce_store = SQLiteRuntimeInventoryNonceStore(nonce_database_path)
    return AuthenticatedRuntimeInventoryProvider(
        probe=probe,
        consume_nonce=nonce_store,
        hmac_key=hmac_key,
        source_id=source_id,
    )

