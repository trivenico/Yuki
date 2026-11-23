"""Managed Unix-socket service lifecycle for the host runtime inventory agent.

This module owns only the local IPC listener. The inventory collector remains
an explicit trusted operator injection; no task-provided command is executed.
"""
from __future__ import annotations

import errno
import os
import socket
import stat
import threading
from pathlib import Path

from core.host_runtime_inventory import RuntimeInventoryError
from core.host_runtime_inventory_ipc import HostRuntimeInventoryAgent


class HostRuntimeInventoryUnixService:
    """Single-request-at-a-time bounded server with safe socket ownership."""

    def __init__(
        self,
        *,
        agent: HostRuntimeInventoryAgent,
        socket_path: str,
        service_uid: int,
        socket_gid: int,
        socket_mode: int = 0o660,
        backlog: int = 16,
        accept_timeout_seconds: float = 0.5,
    ) -> None:
        if not isinstance(agent, HostRuntimeInventoryAgent):
            raise TypeError("a configured HostRuntimeInventoryAgent is required.")
        if type(socket_path) is not str or not os.path.isabs(socket_path):
            raise ValueError("socket_path must be absolute.")
        if type(service_uid) is not int or service_uid < 0 or type(socket_gid) is not int or socket_gid < 0:
            raise ValueError("service UID and socket GID must be non-negative integers.")
        if type(socket_mode) is not int or socket_mode not in (0o600, 0o660):
            raise ValueError("socket_mode must be 0600 or 0660.")
        if type(backlog) is not int or not 1 <= backlog <= 64:
            raise ValueError("backlog must be between 1 and 64.")
        if (type(accept_timeout_seconds) not in (int, float)
                or isinstance(accept_timeout_seconds, bool)
                or not 0.1 <= accept_timeout_seconds <= 2):
            raise ValueError("accept timeout must be between 0.1 and 2 seconds.")
        self.agent = agent
        self.socket_path = socket_path
        self.service_uid = service_uid
        self.socket_gid = socket_gid
        self.socket_mode = socket_mode
        self.backlog = backlog
        self.accept_timeout_seconds = float(accept_timeout_seconds)
        self._listener: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._stop = threading.Event()

    def _validate_parent(self) -> None:
        parent = os.path.dirname(self.socket_path)
        current = Path(parent)
        for component in reversed((current, *current.parents)):
            info = os.stat(component, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RuntimeInventoryError("host-agent socket directory path contains a symlink or non-directory.")
        info = os.stat(parent, follow_symlinks=False)
        if (info.st_uid != self.service_uid or info.st_mode & 0o022):
            raise RuntimeInventoryError(
                "host-agent socket directory must be owned by the service UID and not group/world writable."
            )

    def start(self) -> None:
        if self._listener is not None:
            raise RuntimeError("host-agent service is already started.")
        if os.geteuid() != self.service_uid:
            raise RuntimeInventoryError("host-agent process UID does not match configured service UID.")
        self._validate_parent()
        try:
            existing = os.stat(self.socket_path, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != self.service_uid:
                raise RuntimeInventoryError("refusing to replace a non-owned or non-socket endpoint.")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            try:
                probe.connect(self.socket_path)
            except OSError as exc:
                if exc.errno != errno.ECONNREFUSED:
                    raise RuntimeInventoryError("existing host-agent socket may still be active.") from exc
            else:
                raise RuntimeInventoryError("host-agent socket is already accepting connections.")
            finally:
                probe.close()
            current = os.stat(self.socket_path, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino):
                raise RuntimeInventoryError("host-agent socket changed during stale-endpoint cleanup.")
            os.unlink(self.socket_path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(self.socket_path)
            os.chown(self.socket_path, self.service_uid, self.socket_gid, follow_symlinks=False)
            os.chmod(self.socket_path, self.socket_mode, follow_symlinks=False)
            endpoint = os.stat(self.socket_path, follow_symlinks=False)
            if (not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != self.service_uid
                    or endpoint.st_gid != self.socket_gid
                    or stat.S_IMODE(endpoint.st_mode) != self.socket_mode):
                raise RuntimeInventoryError("host-agent socket ownership or mode could not be established.")
            listener.listen(self.backlog)
            listener.settimeout(self.accept_timeout_seconds)
            self._listener = listener
            self._socket_identity = (endpoint.st_dev, endpoint.st_ino)
            self._stop.clear()
        except Exception:
            listener.close()
            self._unlink_owned_socket()
            raise

    def serve_forever(self) -> None:
        if self._listener is None:
            raise RuntimeError("host-agent service must be started before serving.")
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                raise
            try:
                self.agent.handle_connection(connection)
            except (OSError, RuntimeInventoryError, ValueError, TypeError):
                # Fail this connection closed; never expose collector or key details.
                pass
            finally:
                connection.close()

    def _unlink_owned_socket(self) -> None:
        identity = self._socket_identity
        if identity is None:
            return
        try:
            current = os.stat(self.socket_path, follow_symlinks=False)
            if (stat.S_ISSOCK(current.st_mode)
                    and (current.st_dev, current.st_ino) == identity
                    and current.st_uid == self.service_uid):
                os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        finally:
            self._socket_identity = None

    def close(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        self._unlink_owned_socket()

    def __enter__(self) -> "HostRuntimeInventoryUnixService":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
