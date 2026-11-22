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
class SandboxExecutionProfile:
    """Operator-configured, immutable command profile for one registered agent spec."""

    spec_id: str
    image: str
    policy: SandboxPolicy
    command: tuple[str, ...]
    workspace_resolver: Callable[[object], str]

    def __post_init__(self) -> None:
        if type(self.spec_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.spec_id):
            raise ValueError("sandbox profile spec_id must be a bounded identifier")
        if type(self.image) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("sandbox profile image must be pinned by an immutable sha256 digest")
        if not isinstance(self.policy, SandboxPolicy):
            raise TypeError("sandbox profile policy must be a SandboxPolicy")
        self.policy.validate()
        if type(self.command) is not tuple or not self.command or len(self.command) > 64:
            raise ValueError("sandbox profile command must be a bounded non-empty tuple")
        if any(type(arg) is not str or not arg or len(arg) > 4096 or "\x00" in arg for arg in self.command):
            raise ValueError("sandbox profile command arguments must be bounded strings")
        if not callable(self.workspace_resolver):
            raise TypeError("sandbox profile workspace_resolver must be callable")


@dataclass(frozen=True)
class SandboxExecutionResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool
    output_truncated: bool
    container_id: str
    cancelled: bool = False


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
        socket_stat = os.stat(docker_host_socket, follow_symlinks=False)
        if not stat.S_ISSOCK(socket_mode):
            raise ValueError("docker_host_socket must be a Unix socket")
        if not hasattr(os, "getuid") or socket_stat.st_uid != os.getuid() or socket_stat.st_mode & 0o022:
            raise ValueError("docker_host_socket must be owned by this non-root service user and not world-writable")
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

    def execute_authorized_task(
        self,
        *,
        task: object,
        execution_spec: object,
        profile: SandboxExecutionProfile,
        actor_id: str,
        worker_id: str,
        authorize: Callable[[str, str, str], bool],
        cancellation_check: Callable[[], bool] | None = None,
        lifecycle_callback: Callable[[str, str, SandboxExecutionResult | None], None] | None = None,
    ) -> SandboxExecutionResult:
        """Run only an operator-pinned command for a currently leased, authorized task."""
        from automation.agent_execution_spec import AgentExecutionSpec

        task_id = getattr(task, "task_id", None)
        owner_id = getattr(task, "owner_id", None)
        spec_id = getattr(task, "agent_execution_spec_id", None)
        if (
            not isinstance(owner_id, str) or not owner_id.strip()
            or not isinstance(task_id, str) or not task_id
            or getattr(task, "state", None) != "running"
            or getattr(task, "lease_owner", None) != worker_id
            or not isinstance(execution_spec, AgentExecutionSpec)
            or spec_id != execution_spec.spec_id
            or profile.spec_id != execution_spec.spec_id
        ):
            raise SandboxRuntimeError("task lease or registered execution specification is invalid")
        if "sandbox.execute" not in execution_spec.capabilities:
            raise SandboxRuntimeError("execution specification does not request sandbox execution")
        if (
            execution_spec.budget.max_runtime_seconds > profile.policy.timeout_seconds
            or execution_spec.budget.max_memory_mb > profile.policy.memory_mb
        ):
            raise SandboxRuntimeError("sandbox profile does not satisfy the agent execution budget")
        if not callable(authorize):
            raise TypeError("authorize must be a trusted authorization callback")
        try:
            permitted = authorize(actor_id, "task:sandbox:execute", task_id) is True
        except Exception:
            permitted = False
        if not permitted:
            raise PermissionError("sandbox task execution is not authorized")
        workspace = profile.workspace_resolver(task)
        if type(workspace) is not str or not workspace:
            raise SandboxRuntimeError("trusted workspace resolver returned an invalid path")
        result = self.execute(
            policy=profile.policy, image=profile.image,
            workspace=workspace, command=profile.command,
            cancellation_check=cancellation_check,
            lifecycle_callback=lifecycle_callback,
        )
        if result.timed_out:
            raise SandboxRuntimeError("sandbox execution exceeded its wall-clock deadline")
        if result.output_truncated:
            raise SandboxRuntimeError("sandbox output exceeded its configured limit")
        if result.exit_code != 0:
            raise SandboxRuntimeError("sandbox command exited unsuccessfully")
        return result

    def execute(
        self,
        *,
        policy: SandboxPolicy,
        image: str,
        workspace: str,
        command: tuple[str, ...] | list[str],
        cancellation_check: Callable[[], bool] | None = None,
        lifecycle_callback: Callable[[str, str, SandboxExecutionResult | None], None] | None = None,
    ) -> SandboxExecutionResult:
        policy.validate()
        if policy.max_output_bytes > self.output_limit_bytes:
            raise SandboxRuntimeError("policy output limit exceeds executor output ceiling")
        self._verify(policy, image)
        argv = build_docker_argv(
            policy, image=image, workspace=workspace, command=command,
        )
        name = "yuki-" + uuid.uuid4().hex
        if lifecycle_callback is not None:
            lifecycle_callback("task.sandbox.started", name, None)
        argv[0] = self.docker_binary
        argv[2:2] = [
            "--name", name,
            "--security-opt", f"apparmor={self.apparmor_profile}",
        ]

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        capture_limit = min(policy.max_output_bytes, self.output_limit_bytes)
        total = {"combined": 0}
        capture_lock = threading.Lock()
        truncated = threading.Event()

        def drain(pipe, chunks, key):
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    return
                with capture_lock:
                    remaining = max(0, capture_limit - total["combined"])
                    if remaining:
                        chunks.append(chunk[:remaining])
                    total["combined"] += len(chunk)
                    if total["combined"] > capture_limit:
                        truncated.set()

        if cancellation_check is not None and not callable(cancellation_check):
            raise TypeError("cancellation_check must be callable")
        if lifecycle_callback is not None and not callable(lifecycle_callback):
            raise TypeError("lifecycle_callback must be callable")
        started = time.monotonic()
        process = None
        timed_out = False
        cancelled = False
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
                if cancellation_check is not None:
                    try:
                        cancelled = cancellation_check() is True
                    except Exception:
                        cancelled = True
                    if cancelled:
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
        except Exception:
            if lifecycle_callback is not None:
                try:
                    lifecycle_callback("task.sandbox.failed", name, None)
                except Exception:
                    # Preserve the original execution exception; the task worker
                    # will fail closed if the audit write could not be completed.
                    pass
            raise
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
            if lifecycle_callback is not None:
                lifecycle_callback("task.sandbox.failed", name, None)
            raise SandboxRuntimeError("container cleanup could not be verified; operator reconciliation is required")

        execution_result = SandboxExecutionResult(
            exit_code=exit_code, stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            duration_seconds=max(0.0, time.monotonic() - started),
            timed_out=timed_out, output_truncated=truncated.is_set(),
            container_id=name, cancelled=cancelled,
        )
        if lifecycle_callback is not None:
            lifecycle_kind = (
                "task.sandbox.cancelled" if cancelled else
                "task.sandbox.completed" if exit_code == 0 and not timed_out and not truncated.is_set()
                else "task.sandbox.failed"
            )
            lifecycle_callback(lifecycle_kind, name, execution_result)
        return execution_result

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
