"""Operator-verified Docker execution with bounded process and container lifecycle.

This module is intentionally opt-in. A deployment must inject a trusted
host-side verifier; Docker daemon self-reported metadata is not attestation.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import signal
import socket
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import SandboxRuntimeError, build_docker_argv


@dataclass(frozen=True)
class SandboxVerification:
    """Evidence returned by an operator-trusted host verifier.

    The verifier is the trust root. This value is not generated from Docker
    info JSON and must only be returned after host-side control verification.
    """
    verifier_id: str
    image: str
    policy_sha256: str
    controls: frozenset[str]
    expires_at: float
    apparmor_profile: str


@dataclass(frozen=True)
class SandboxExecutionResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool
    output_truncated: bool
    container_id: str


_REQUIRED_CONTROLS = frozenset({
    "rootless", "user_namespace", "pid_namespace", "mount_namespace",
    "ipc_namespace", "network_namespace", "seccomp", "mandatory_access_control",
    "cgroups_v2", "network_isolation", "read_only_root", "workspace_mount_policy",
    "host_secret_exclusion", "cpu_limit", "memory_limit", "process_limit",
    "ephemeral_cleanup",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DockerSandboxExecutor:
    """Execute pinned-image tasks only after trusted host verification."""

    def __init__(
        self,
        *,
        docker_binary: str,
        runtime_verifier: Callable[[SandboxPolicy, str], SandboxVerification],
        output_limit_bytes: int = 1_048_576,
        cleanup_timeout_seconds: float = 5.0,
        apparmor_profile: str = "docker-default",
        docker_host_socket: str,
    ) -> None:
        if type(docker_binary) is not str or not os.path.isabs(docker_binary):
            raise ValueError("docker_binary must be an absolute executable path")
        if os.path.islink(docker_binary) or not os.path.isfile(docker_binary) or not os.access(docker_binary, os.X_OK):
            raise ValueError("docker_binary must be a non-symlink executable file")
        if not callable(runtime_verifier):
            raise TypeError("runtime_verifier must be an operator-trusted callable")
        if type(output_limit_bytes) is not int or not 1 <= output_limit_bytes <= 16_777_216:
            raise ValueError("output_limit_bytes is outside the permitted range")
        if isinstance(cleanup_timeout_seconds, bool) or not isinstance(cleanup_timeout_seconds, (int, float)) or not 0.1 <= cleanup_timeout_seconds <= 30:
            raise ValueError("cleanup_timeout_seconds is outside the permitted range")
        if type(docker_host_socket) is not str or not os.path.isabs(docker_host_socket):
            raise ValueError("docker_host_socket must be an absolute Unix socket path")
        if os.path.islink(docker_host_socket):
            raise ValueError("docker_host_socket must not be a symlink")
        try:
            socket_mode = os.stat(docker_host_socket, follow_symlinks=False).st_mode
        except OSError as exc:
            raise ValueError("docker_host_socket must exist and be a Unix socket") from exc
        if not stat.S_ISSOCK(socket_mode):
            raise ValueError("docker_host_socket must be a Unix socket")
        self.docker_host = "unix://" + os.path.realpath(docker_host_socket)
        self.docker_binary = os.path.realpath(docker_binary)
        self.runtime_verifier = runtime_verifier
        self.output_limit_bytes = output_limit_bytes
        self.cleanup_timeout_seconds = float(cleanup_timeout_seconds)
        if type(apparmor_profile) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", apparmor_profile):
            raise ValueError("apparmor_profile must be a bounded profile identifier")
        self.apparmor_profile = apparmor_profile

    @staticmethod
    def _policy_digest(policy: SandboxPolicy) -> str:
        fields = (
            policy.network_mode, tuple(sorted(policy.capabilities)),
            policy.timeout_seconds, policy.memory_mb, policy.cpu_millis,
            policy.max_output_bytes, policy.max_processes,
            policy.read_only_root, policy.run_as_non_root,
        )
        return hashlib.sha256(repr(fields).encode("utf-8")).hexdigest()

    def _verify(self, policy: SandboxPolicy, image: str) -> None:
        evidence = self.runtime_verifier(policy, image)
        if not isinstance(evidence, SandboxVerification):
            raise SandboxRuntimeError("trusted runtime verifier returned no valid verification evidence")
        if (
            type(evidence.verifier_id) is not str
            or not evidence.verifier_id.strip() or len(evidence.verifier_id) > 120
            or type(evidence.image) is not str or evidence.image != image
            or type(evidence.apparmor_profile) is not str
            or evidence.apparmor_profile != self.apparmor_profile
            or type(evidence.policy_sha256) is not str
            or not _SHA256_RE.fullmatch(evidence.policy_sha256)
            or evidence.policy_sha256 != self._policy_digest(policy)
            or type(evidence.controls) is not frozenset
            or any(type(item) is not str for item in evidence.controls)
            or not _REQUIRED_CONTROLS <= evidence.controls
            or isinstance(evidence.expires_at, bool)
            or not isinstance(evidence.expires_at, (int, float))
            or not math.isfinite(evidence.expires_at)
            or evidence.expires_at <= time.time()
            or evidence.expires_at > time.time() + 3600
        ):
            raise SandboxRuntimeError("runtime verification evidence is invalid, incomplete, or expired")

    def execute(
        self,
        *,
        policy: SandboxPolicy,
        image: str,
        workspace: str,
        command: tuple[str, ...] | list[str],
    ) -> SandboxExecutionResult:
        policy.validate()
        if policy.max_output_bytes > self.output_limit_bytes:
            raise SandboxRuntimeError("policy output limit exceeds executor output ceiling")
        self._verify(policy, image)
        argv = build_docker_argv(
            policy, image=image, workspace=workspace, command=command,
        )
        name = "yuki-" + uuid.uuid4().hex
        argv[0] = self.docker_binary
        argv[2:2] = [
            "--name", name,
            "--security-opt", "seccomp=default",
            "--security-opt", f"apparmor={self.apparmor_profile}",
        ]

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        total = {"combined": 0}
        truncated = threading.Event()

        def drain(pipe, chunks, key):
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    return
                remaining = max(0, self.output_limit_bytes - total["combined"])
                if remaining:
                    chunks.append(chunk[:remaining])
                total["combined"] += len(chunk)
                if total["combined"] > self.output_limit_bytes:
                    truncated.set()

        started = time.monotonic()
        process = None
        timed_out = False
        exit_code = -1
        cleanup_ok = True
        try:
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
                env={"PATH": os.path.dirname(self.docker_binary), "LANG": "C.UTF-8", "DOCKER_HOST": self.docker_host},
            )
            out_thread = threading.Thread(target=drain, args=(process.stdout, stdout_chunks, "stdout"), daemon=True)
            err_thread = threading.Thread(target=drain, args=(process.stderr, stderr_chunks, "stderr"), daemon=True)
            out_thread.start()
            err_thread.start()
            deadline = started + policy.timeout_seconds
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=1.0)
                    break
                time.sleep(0.02)
            if process.poll() is not None:
                exit_code = int(process.returncode)
            out_thread.join(timeout=2.0)
            err_thread.join(timeout=2.0)
            if out_thread.is_alive() or err_thread.is_alive():
                raise SandboxRuntimeError("sandbox output streams did not close")
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
            cleanup_ok = self._cleanup_container(name)
        if not cleanup_ok:
            raise SandboxRuntimeError("container cleanup could not be verified; operator reconciliation is required")

        return SandboxExecutionResult(
            exit_code=exit_code, stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            duration_seconds=max(0.0, time.monotonic() - started),
            timed_out=timed_out, output_truncated=truncated.is_set(),
            container_id=name,
        )

    def _cleanup_container(self, name: str) -> bool:
        try:
            result = subprocess.run(
                [self.docker_binary, "rm", "-f", name],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, close_fds=True,
                timeout=self.cleanup_timeout_seconds, check=False,
                env={"PATH": os.path.dirname(self.docker_binary), "LANG": "C.UTF-8", "DOCKER_HOST": self.docker_host},
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode == 0:
            return True
        # docker run --rm normally removes a completed container itself.
        # Accept only Docker's explicit absent-container response, not daemon,
        # permission, transport, or arbitrary CLI failures.
        diagnostic = (result.stderr or b"").decode("utf-8", errors="replace").lower()
        return result.returncode == 1 and "no such container" in diagnostic
