"""Docker-backed runtime adapter for isolated sandbox artifact jobs.

The adapter launches detached, digest-pinned containers with a private output
mount. A job is frozen only after Docker inspection confirms the container is
stopped and Docker successfully removes the stopped container.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import build_docker_argv


_IMAGE_DIGEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}@sha256:[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")


class DockerJobRuntimeError(RuntimeError):
    """Docker could not safely launch or confirm termination of a job."""


@dataclass(frozen=True)
class DockerJobHandle:
    job_id: str
    container_name: str


class DockerJobRuntime:
    """Launch and freeze bounded Docker jobs using fixed host-owned settings.

    The command runner is injectable for deterministic tests. Production use
    defaults to subprocess.run with shell disabled and a strict timeout.
    """

    def __init__(
        self,
        *,
        workspace: str | Path,
        image: str,
        command: Sequence[str],
        policy: SandboxPolicy | None = None,
        docker_binary: str = "docker",
        command_timeout_seconds: int = 15,
        runner: Callable[..., object] | None = None,
    ):
        if not isinstance(image, str) or not _IMAGE_DIGEST_RE.fullmatch(image):
            raise ValueError("sandbox image must be pinned to a lowercase sha256 digest")
        if not isinstance(command, (tuple, list)) or not command or any(
            not isinstance(part, str) or not part or "\x00" in part or len(part) > 500
            for part in command
        ):
            raise ValueError("sandbox command must be a fixed non-empty argv sequence")
        if not isinstance(docker_binary, str) or not docker_binary or "\x00" in docker_binary:
            raise ValueError("invalid Docker executable")
        if type(command_timeout_seconds) is not int or not 1 <= command_timeout_seconds <= 60:
            raise ValueError("Docker command timeout must be between 1 and 60 seconds")
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("trusted workspace must be an existing directory")
        self.image = image
        self.command = tuple(command)
        self.policy = (policy or SandboxPolicy()).validate()
        self.docker_binary = docker_binary
        self.command_timeout_seconds = command_timeout_seconds
        self._runner = runner or subprocess.run

    def _run(self, argv: list[str]):
        try:
            result = self._runner(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self.command_timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DockerJobRuntimeError("Docker command could not be completed safely") from exc
        if getattr(result, "returncode", 1) != 0:
            raise DockerJobRuntimeError("Docker command failed")
        return result

    @staticmethod
    def handle_for_job(job_id: str) -> DockerJobHandle:
        """Reconstruct only the deterministic managed handle during recovery."""
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise DockerJobRuntimeError("invalid sandbox job identifier")
        return DockerJobHandle(job_id=job_id, container_name="yuki-job-" + job_id[4:])

    def launch(self, job_id: str, staging_directory: str) -> DockerJobHandle:
        handle = self.handle_for_job(job_id)
        container_name = handle.container_name
        try:
            argv = build_docker_argv(
                self.policy,
                image=self.image,
                workspace=str(self.workspace),
                command=self.command,
                artifact_output_directory=staging_directory,
            )
        except Exception as exc:
            raise DockerJobRuntimeError("sandbox Docker policy could not be built") from exc

        # Convert the foreground builder command to a detached managed container.
        # Do not pass caller-controlled Docker flags or shell fragments.
        if "--rm" not in argv or "--interactive" not in argv:
            raise DockerJobRuntimeError("unexpected Docker command contract")
        argv.remove("--rm")
        argv.remove("--interactive")
        argv[2:2] = ["--detach", "--name", container_name]
        self._run(argv)
        return handle


    def _inspect_running_state(self, container_name: str) -> str:
        """Return running/stopped/missing; daemon and transport errors stay errors."""
        try:
            result = self._runner(
                [self.docker_binary, "inspect", "--format={{.State.Running}}", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.command_timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DockerJobRuntimeError("Docker inspection could not be completed safely") from exc

        returncode = getattr(result, "returncode", 1)
        stdout = getattr(result, "stdout", b"")
        stderr = getattr(result, "stderr", b"")
        if isinstance(stdout, bytes):
            output = stdout.decode("ascii", errors="strict").strip()
        elif isinstance(stdout, str):
            output = stdout.strip()
        else:
            raise DockerJobRuntimeError("Docker inspect returned invalid output")

        if returncode != 0:
            if isinstance(stderr, bytes):
                error_text = stderr.decode("utf-8", errors="replace").strip()
            elif isinstance(stderr, str):
                error_text = stderr.strip()
            else:
                error_text = ""
            if error_text in {
                f"Error: No such object: {container_name}",
                f"Error: No such container: {container_name}",
            }:
                return "missing"
            raise DockerJobRuntimeError("Docker could not establish container state")

        if output == "true":
            return "running"
        if output == "false":
            return "stopped"
        raise DockerJobRuntimeError("Docker inspect returned an unknown container state")

    def reconcile(self, handle: object) -> str:
        """Reconcile a deterministic job container after a host restart.

        Returns missing only for Docker's exact no-such-object response, or
        removed after a stopped container has been removed. Ambiguous failures
        raise and must leave durable job data intact.
        """
        if not isinstance(handle, DockerJobHandle) or not _JOB_ID_RE.fullmatch(handle.job_id):
            raise DockerJobRuntimeError("invalid Docker job handle")
        expected_name = "yuki-job-" + handle.job_id[4:]
        if handle.container_name != expected_name:
            raise DockerJobRuntimeError("Docker container name does not match job identity")

        state = self._inspect_running_state(expected_name)
        if state == "missing":
            return "missing"
        if state == "running":
            try:
                self._runner(
                    [self.docker_binary, "stop", "--time", "2", expected_name],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.command_timeout_seconds,
                    check=False,
                    shell=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            state = self._inspect_running_state(expected_name)
            if state != "stopped":
                raise DockerJobRuntimeError("Docker did not confirm the job container stopped")
        elif state != "stopped":
            raise DockerJobRuntimeError("Docker container state could not be reconciled")

        self._run([self.docker_binary, "rm", expected_name])
        return "removed"

    def freeze(self, handle: object) -> bool:
        if not isinstance(handle, DockerJobHandle) or not _JOB_ID_RE.fullmatch(handle.job_id):
            raise DockerJobRuntimeError("invalid Docker job handle")
        expected_name = "yuki-job-" + handle.job_id[4:]
        if handle.container_name != expected_name:
            raise DockerJobRuntimeError("Docker container name does not match job identity")

        # Stop is idempotence-tolerant: if it fails (e.g. already exited), inspect
        # still decides whether it is safe to proceed.
        try:
            self._runner(
                [self.docker_binary, "stop", "--time", "2", expected_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.command_timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

        inspected = self._run([
            self.docker_binary, "inspect", "--format={{.State.Running}}", expected_name
        ])
        output = getattr(inspected, "stdout", b"")
        if isinstance(output, bytes):
            state = output.decode("ascii", errors="ignore").strip()
        elif isinstance(output, str):
            state = output.strip()
        else:
            raise DockerJobRuntimeError("Docker inspect returned an invalid state")
        if state != "false":
            raise DockerJobRuntimeError("Docker did not confirm the job container is stopped")

        # Remove the stopped container to eliminate its writable mount namespace.
        self._run([self.docker_binary, "rm", expected_name])
        return True
