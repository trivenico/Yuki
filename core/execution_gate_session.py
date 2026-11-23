"""Per-execution composition for the private gate authorization handoff.

Create one session before Docker container creation, pass its private directory
to the gate create builder, and inject its binder/releaser into the trusted
Docker supervisor collector. This module does not launch Docker or replace the
existing direct executor.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Callable, Sequence

from core.execution_gate_identity import ExecutionGateIdentityBinder
from core.execution_gate_protocol import ExecutionGateBinding, issue_execution_gate_authorization
from core.execution_gate_transport import ExecutionGateAuthorizationListener


class ExecutionGateSessionError(RuntimeError):
    """A per-execution gate handoff could not be completed safely."""


class ExecutionGateAuthorizationSession:
    """Own one listener, identity binding, signed release and exec observation."""

    def __init__(
        self,
        *,
        private_gate_directory: str,
        task_id: str,
        worker_id: str,
        spec_id: str,
        image: str,
        expected_host_uid: int,
        uid_resolver: Callable[[int], int],
        exec_observer: Callable[..., bool],
        listener_timeout_seconds: float = 30.0,
        exec_observation_timeout_seconds: float = 2.0,
        key_factory: Callable[[], bytes] = secrets.token_bytes,
        listener_factory: Callable[..., object] = ExecutionGateAuthorizationListener,
    ) -> None:
        if type(private_gate_directory) is not str or not os.path.isabs(private_gate_directory):
            raise ValueError("private_gate_directory must be absolute")
        directory = Path(private_gate_directory)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("private gate directory must be an existing non-symlink directory")
        resolved = directory.resolve(strict=True)
        if resolved.stat().st_uid != os.getuid() or resolved.stat().st_mode & 0o077:
            raise ValueError("private gate directory must be service-owned and private")
        for name, value in (("task_id", task_id), ("worker_id", worker_id), ("spec_id", spec_id)):
            if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value):
                raise ValueError(f"{name} is invalid")
        if type(image) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}", image
        ):
            raise ValueError("image must be pinned by an immutable sha256 digest")
        if type(expected_host_uid) is not int or expected_host_uid <= 0:
            raise ValueError("expected_host_uid must be a non-root UID")
        if not callable(uid_resolver) or not callable(exec_observer):
            raise TypeError("trusted UID resolver and exec observer are required")
        if type(listener_timeout_seconds) not in (int, float) or isinstance(listener_timeout_seconds, bool) or not 0.1 <= listener_timeout_seconds <= 120:
            raise ValueError("listener_timeout_seconds must be between 0.1 and 120")
        if type(exec_observation_timeout_seconds) not in (int, float) or isinstance(exec_observation_timeout_seconds, bool) or not 0.05 <= exec_observation_timeout_seconds <= 10:
            raise ValueError("exec observation timeout must be between 0.05 and 10")
        key = key_factory(32)
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("key factory must return exactly 32 cryptographically random bytes")
        self.private_gate_directory = str(resolved)
        self.socket_path = str(resolved / "authorization.sock")
        self.binding_fields = (task_id, worker_id, spec_id, image)
        self.expected_host_uid = expected_host_uid
        self.exec_observer = exec_observer
        self.exec_observation_timeout_seconds = float(exec_observation_timeout_seconds)
        self._key: bytes | None = key
        self._gate_pid: int | None = None
        self._gate_uid: int | None = None
        self._released = False
        self._closed = False
        self.listener = listener_factory(
            socket_path=self.socket_path, timeout_seconds=float(listener_timeout_seconds),
        )
        self._identity_binder = ExecutionGateIdentityBinder(
            listener=self.listener, uid_resolver=uid_resolver,
        )

    @property
    def gate_arguments(self) -> tuple[str, ...]:
        """Exact image-owned gate startup arguments for the matching create command."""
        task_id, worker_id, spec_id, image = self.binding_fields
        return (
            "--socket", "/run/yuki-gate/authorization.sock",
            "--host-uid", str(self.expected_host_uid),
            "--task-id", task_id,
            "--worker-id", worker_id,
            "--spec-id", spec_id,
            "--image", image,
            "--timeout", str(self.listener.timeout_seconds),
        )

    def open(self) -> "ExecutionGateAuthorizationSession":
        if self._closed:
            raise ExecutionGateSessionError("gate session is already closed")
        self.listener.open()
        return self

    def bind_gate_identity(self, container_id: str, host_pid: int, inspected_container: dict) -> None:
        if self._closed or self._released or self._gate_pid is not None:
            raise ExecutionGateSessionError("gate identity binding is closed or already consumed")
        self._identity_binder(container_id, host_pid, inspected_container)
        self._gate_pid = host_pid
        self._gate_uid = self.listener.expected_gate_uid

    def release(
        self,
        task_id: str,
        worker_id: str,
        container_id: str,
        spec_id: str,
        image: str,
        command: Sequence[str],
        evidence: object,
    ) -> None:
        if self._closed or self._released or self._key is None:
            raise ExecutionGateSessionError("gate authorization session is unavailable or already consumed")
        if self._gate_pid is None or self._gate_uid is None:
            raise ExecutionGateSessionError("gate process identity has not been bound")
        if (task_id, worker_id, spec_id, image) != self.binding_fields:
            raise ExecutionGateSessionError("release request does not match the prepared execution")
        binding = ExecutionGateBinding(
            task_id=task_id, worker_id=worker_id, container_id=container_id,
            spec_id=spec_id, image=image,
        )
        token = issue_execution_gate_authorization(
            key=self._key, binding=binding, command=command,
        )
        key = self._key
        self._key = None
        self._released = True
        try:
            self.listener.deliver(token=token, key=key)
        except Exception as exc:
            raise ExecutionGateSessionError("gate did not verify the signed authorization") from exc
        try:
            observed = self.exec_observer(
                self._gate_pid,
                expected_uid=self._gate_uid,
                expected_argv=tuple(command),
                timeout_seconds=self.exec_observation_timeout_seconds,
            )
        except Exception as exc:
            raise ExecutionGateSessionError("authorized task exec could not be confirmed") from exc
        if observed is not True:
            raise ExecutionGateSessionError("authorized task exec was not observed before the deadline")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._key = None
        self.listener.close()

    def __enter__(self) -> "ExecutionGateAuthorizationSession":
        return self.open()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
