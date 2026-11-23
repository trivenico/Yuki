"""Opt-in gate-first Docker execution orchestration.

This path is deliberately separate from DockerSandboxExecutor.execute(). It
creates a gate-only container, obtains trusted live evidence, releases the
execution-bound authorization, and delegates bounded post-exec monitoring to
an operator-supplied monitor. There is no direct-command fallback.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
import uuid
from typing import Callable

from automation.agent_execution_spec import AgentExecutionSpec
from core.execution_gate_docker import build_gate_container_create_argv
from core.execution_gate_docker_lifecycle import CreatedGateContainer, DockerGateContainerClient
from core.execution_gate_session import ExecutionGateAuthorizationSession
from core.gate_first_post_exec_monitor import DockerGatePostExecMonitor
from core.sandbox_executor import SandboxExecutionProfile, SandboxExecutionResult
from core.sandbox_policy import SandboxPolicy


class GateFirstExecutionError(RuntimeError):
    """Gate-first execution failed closed or could not be monitored safely."""


class GateFirstDockerSandboxExecutor:
    """Compose the gate session, Docker lifecycle, evidence collector and monitor.

    The monitor is mandatory and must bound output, wall time, cancellation and
    lease-loss handling. Container removal and private-directory cleanup remain
    owned by this class and run for every outcome.
    """

    def __init__(
        self,
        *,
        docker_client: DockerGateContainerClient,
        docker_binary: str,
        apparmor_profile: str,
        private_directory_root: str,
        expected_gate_host_uid: int,
        uid_resolver: Callable[[int], int],
        exec_observer: Callable[..., bool],
        supervisor_collector_factory: Callable[..., object],
        host_preflight: Callable[[], object] | None = None,
        post_exec_monitor: Callable[..., SandboxExecutionResult] | None = None,
        session_factory: Callable[..., ExecutionGateAuthorizationSession] = ExecutionGateAuthorizationSession,
        listener_timeout_seconds: float = 30.0,
        exec_observation_timeout_seconds: float = 2.0,
    ) -> None:
        if not isinstance(docker_client, DockerGateContainerClient):
            raise TypeError("docker_client must be a DockerGateContainerClient")
        if type(docker_binary) is not str or not os.path.isabs(docker_binary):
            raise ValueError("docker_binary must be absolute")
        if type(apparmor_profile) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", apparmor_profile):
            raise ValueError("apparmor_profile is invalid")
        if type(private_directory_root) is not str or not os.path.isabs(private_directory_root):
            raise ValueError("private_directory_root must be absolute")
        root = os.path.realpath(private_directory_root)
        info = os.stat(root, follow_symlinks=False)
        if os.path.islink(private_directory_root) or not os.path.isdir(root):
            raise ValueError("private_directory_root must be an existing non-symlink directory")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("private_directory_root must be service-owned and mode 0700 or stricter")
        if type(expected_gate_host_uid) is not int or expected_gate_host_uid <= 0:
            raise ValueError("expected_gate_host_uid must be a non-root host UID")
        for name, callback in (
            ("uid_resolver", uid_resolver),
            ("exec_observer", exec_observer),
            ("supervisor_collector_factory", supervisor_collector_factory),
            ("session_factory", session_factory),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable")
        self.docker_client = docker_client
        self.docker_binary = os.path.realpath(docker_binary)
        self.apparmor_profile = apparmor_profile
        self.private_directory_root = root
        self.expected_gate_host_uid = expected_gate_host_uid
        self.uid_resolver = uid_resolver
        self.exec_observer = exec_observer
        self.supervisor_collector_factory = supervisor_collector_factory
        if host_preflight is not None and not callable(host_preflight):
            raise TypeError("host_preflight must be callable when supplied")
        self.host_preflight = host_preflight
        if post_exec_monitor is not None and not callable(post_exec_monitor):
            raise TypeError("post_exec_monitor must be callable when supplied")
        self.post_exec_monitor = post_exec_monitor or DockerGatePostExecMonitor(
            docker_client=docker_client,
            output_limit_bytes=min(65_536, 16_777_216),
        )
        self.session_factory = session_factory
        self.listener_timeout_seconds = float(listener_timeout_seconds)
        self.exec_observation_timeout_seconds = float(exec_observation_timeout_seconds)

    def validate_deployment_host(self) -> object:
        """Run the operator-owned host preflight before worker service startup."""
        if not callable(self.host_preflight):
            raise GateFirstExecutionError("gate-first deployment requires an operator-owned host preflight")
        try:
            report = self.host_preflight()
        except Exception as exc:
            raise GateFirstExecutionError("gate-first host preflight failed") from exc
        required = {
            "platform": "linux",
            "user_namespace_mapping_verified": True,
            "release_publisher_ownership_verified": True,
            "trusted_observer_configuration_verified": True,
            "host_procfs_visibility_verified": True,
            "host_capabilities_verified": True,
            "trusted_identity_callbacks": True,
            "trusted_supervisor_collector": True,
            "trusted_evidence_builder": True,
        }
        if not isinstance(report, dict) or any(report.get(key) != value for key, value in required.items()):
            raise GateFirstExecutionError("gate-first host preflight report is incomplete or untrusted")
        if report.get("service_uid") != self.expected_gate_host_uid:
            raise GateFirstExecutionError("gate-first host preflight service UID does not match executor identity")
        if report.get("docker_binary") != self.docker_binary:
            raise GateFirstExecutionError("gate-first host preflight Docker executable does not match executor")
        if report.get("docker_socket") != self.docker_client.docker_host.removeprefix("unix://"):
            raise GateFirstExecutionError("gate-first host preflight Docker socket does not match lifecycle client")
        return report

    def execute(
        self,
        *,
        task: object,
        worker_id: str,
        execution_spec: AgentExecutionSpec,
        profile: SandboxExecutionProfile,
        cancellation_check: Callable[[], bool] | None = None,
        lifecycle_callback: Callable[[str, str, SandboxExecutionResult | None], None] | None = None,
    ) -> SandboxExecutionResult:
        task_id = getattr(task, "run_id", None) or getattr(task, "task_id", None)
        owner_id = getattr(task, "owner_id", None)
        if (
            type(task_id) is not str or not task_id
            or type(owner_id) is not str or not owner_id
            or getattr(task, "state", None) != "running"
            or getattr(task, "lease_owner", None) != worker_id
            or not isinstance(execution_spec, AgentExecutionSpec)
            or getattr(task, "agent_execution_spec_id", None) != execution_spec.spec_id
            or profile.spec_id != execution_spec.spec_id
        ):
            raise GateFirstExecutionError("task lease or immutable execution specification is invalid")
        if cancellation_check is not None and not callable(cancellation_check):
            raise TypeError("cancellation_check must be callable")
        if lifecycle_callback is not None and not callable(lifecycle_callback):
            raise TypeError("lifecycle_callback must be callable")
        if "sandbox.execute" not in execution_spec.capabilities:
            raise GateFirstExecutionError("execution specification does not request sandbox execution")
        if (
            execution_spec.budget.max_runtime_seconds > profile.policy.timeout_seconds
            or execution_spec.budget.max_memory_mb > profile.policy.memory_mb
        ):
            raise GateFirstExecutionError("sandbox profile does not satisfy the execution budget")
        profile.policy.validate()

        private_dir = tempfile.mkdtemp(prefix="yuki-gate-", dir=self.private_directory_root)
        os.chmod(private_dir, 0o700)
        container_name = "yuki-" + uuid.uuid4().hex
        container: CreatedGateContainer | None = None
        session = None
        cleanup_ok = True
        started = time.monotonic()
        lifecycle_started = False
        terminal_event_attempted = False
        try:
            session = self.session_factory(
                private_gate_directory=private_dir,
                task_id=task_id,
                worker_id=worker_id,
                spec_id=profile.spec_id,
                image=profile.image,
                expected_host_uid=self.expected_gate_host_uid,
                uid_resolver=self.uid_resolver,
                exec_observer=self.exec_observer,
                listener_timeout_seconds=self.listener_timeout_seconds,
                exec_observation_timeout_seconds=self.exec_observation_timeout_seconds,
            )
            session.open()
            labels = {
                "yuki.managed_by": "yuki",
                "yuki.task_id": task_id,
                "yuki.worker_id": worker_id,
                "yuki.owner_sha256": hashlib.sha256(owner_id.encode("utf-8")).hexdigest(),
                "yuki.profile_id": profile.spec_id,
                "yuki.image_digest": profile.image.rsplit("@", 1)[-1],
            }
            create_argv = build_gate_container_create_argv(
                docker_binary=self.docker_binary,
                policy=profile.policy,
                image=profile.image,
                workspace=profile.workspace_resolver(task),
                gate_arguments=session.gate_arguments,
                private_gate_directory=session.private_gate_directory,
                container_name=container_name,
                apparmor_profile=self.apparmor_profile,
                container_labels=labels,
            )
            if cancellation_check is not None and cancellation_check() is True:
                raise GateFirstExecutionError("task was cancelled before gate container creation")
            container = self.docker_client.create(argv=create_argv, container_name=container_name)
            self.docker_client.verify_created_container(
                container=container, expected_image=profile.image,
                expected_labels=labels,
            )
            self.docker_client.start(container)

            collector = self.supervisor_collector_factory(
                gate_identity_binder=session.bind_gate_identity,
                gate_releaser=session.release,
            )
            snapshot = collector(
                task_id, worker_id, container.name, execution_spec, profile,
            )
            # Collector returns only after the session has observed the exact
            # authorized argv in the same gate PID. This is the first safe
            # point to publish task.sandbox.started.
            if lifecycle_callback is not None:
                lifecycle_callback("task.sandbox.started", container.container_id, None)
                lifecycle_started = True
            if cancellation_check is not None and cancellation_check() is True:
                raise GateFirstExecutionError("task lease was cancelled after gate release")
            result = self.post_exec_monitor(
                container=container,
                snapshot=snapshot,
                task=task,
                execution_spec=execution_spec,
                profile=profile,
                started_at=started,
                cancellation_check=cancellation_check,
            )
            if not isinstance(result, SandboxExecutionResult):
                raise GateFirstExecutionError("post-exec monitor returned an invalid result")
            if result.container_id != container.container_id:
                raise GateFirstExecutionError("post-exec monitor returned a mismatched container identity")
            if lifecycle_callback is not None:
                kind = "task.sandbox.cancelled" if result.cancelled else (
                    "task.sandbox.completed" if result.exit_code == 0 and not result.timed_out and not result.output_truncated
                    else "task.sandbox.failed"
                )
                terminal_event_attempted = True
                lifecycle_callback(kind, container.container_id, result)
            if result.timed_out or result.cancelled or result.output_truncated or result.exit_code != 0:
                raise GateFirstExecutionError("gate-first task did not complete within its verified execution bounds")
            return result
        except BaseException as execution_error:
            if lifecycle_started and not terminal_event_attempted and lifecycle_callback is not None:
                terminal_event_attempted = True
                try:
                    cancelled = cancellation_check is not None and cancellation_check() is True
                except BaseException:
                    cancelled = True
                try:
                    lifecycle_callback(
                        "task.sandbox.cancelled" if cancelled else "task.sandbox.failed",
                        container.container_id,
                        None,
                    )
                except BaseException as lifecycle_error:
                    raise GateFirstExecutionError(
                        "sandbox terminal lifecycle event could not be recorded"
                    ) from lifecycle_error
            raise execution_error
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    cleanup_ok = False
            if container is not None:
                try:
                    cleanup_ok = self.docker_client.remove_container(container_id=container.container_id) and cleanup_ok
                except BaseException:
                    cleanup_ok = False
            try:
                shutil.rmtree(private_dir)
            except FileNotFoundError:
                pass
            except BaseException:
                cleanup_ok = False
            if not cleanup_ok:
                raise GateFirstExecutionError(
                    "gate-first cleanup could not be verified; operator reconciliation is required"
                )
