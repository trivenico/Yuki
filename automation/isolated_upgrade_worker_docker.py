"""Rootless Docker adapter for bounded, network-disabled upgrade test execution.

This adapter shares the audited snapshot, approval, artifact, timeout and audit
contract with the Podman runner, but verifies and invokes Docker explicitly.
It fails closed unless the Docker daemon reports rootless mode and the worker
image is pinned by an immutable SHA-256 digest.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from automation.authorized_read_tools import ToolAuthorizationError
from automation.isolated_upgrade_worker import (
    PodmanIsolatedTestRunner,
)


class DockerIsolatedTestRunner(PodmanIsolatedTestRunner):
    """Execute allow-listed unittest modules in a rootless Docker container."""

    def __init__(self, workspace: str | Path, *, docker_binary: str = "docker", **kwargs):
        super().__init__(workspace, podman_binary=docker_binary, **kwargs)

    def _runtime_env(self) -> dict[str, str]:
        """Preserve only operator-selected variables needed to reach rootless Docker."""
        allowed = ("PATH", "LANG", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT",
                   "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
        return {key: os.environ[key] for key in allowed if key in os.environ}

    def _ensure_container_removed(self, binary: str, cidfile: Path) -> None:
        """Use Docker-compatible inspection and verify cleanup; fail closed on daemon errors."""
        if not cidfile.is_file():
            raise RuntimeError("worker container ID unavailable; cleanup cannot be verified.")
        container_id = cidfile.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise RuntimeError("worker container ID is malformed; cleanup cannot be verified.")

        def listed() -> list[str]:
            result = subprocess.run(
                [binary, "container", "ls", "--all", "--no-trunc",
                 "--filter", "id=" + container_id, "--format", "{{.ID}}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=5, check=False, shell=False, env=self._runtime_env(),
            )
            if result.returncode != 0:
                raise RuntimeError("unable to verify worker container state.")
            return [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines()
                    if line.strip()]

        try:
            matches = listed()
            if not matches:
                return
            if any(not item.startswith(container_id) for item in matches):
                raise RuntimeError("container ID lookup returned an unexpected container.")
            removed = subprocess.run(
                [binary, "rm", "--force", container_id], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False,
                shell=False, env=self._runtime_env(),
            )
            if removed.returncode != 0:
                raise RuntimeError("worker container removal failed.")
            if listed():
                raise RuntimeError("worker container cleanup could not be confirmed.")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("worker container cleanup could not be confirmed.") from exc

    def _verify_rootless_runtime(self, binary: str) -> None:
        try:
            result = subprocess.run(
                [binary, "info", "--format", "{{json .SecurityOptions}}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
                shell=False,
                env=self._runtime_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolAuthorizationError("rootless Docker runtime is unavailable.") from exc
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise ToolAuthorizationError("Docker daemon health check failed.")
        try:
            options = json.loads(result.stdout.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise ToolAuthorizationError("unable to verify rootless Docker mode.") from exc
        if not isinstance(options, list) or not any(
            isinstance(option, str) and "rootless" in option.casefold()
            for option in options
        ):
            raise ToolAuthorizationError("isolated test execution requires rootless Docker.")

    def _build_container_command(
        self, *, binary: str, cidfile: Path, artifact_path: Path,
        artifact_digest: str, test_module: str,
    ) -> list[str]:
        command = super()._build_container_command(
            binary=binary,
            cidfile=cidfile,
            artifact_path=artifact_path,
            artifact_digest=artifact_digest,
            test_module=test_module,
        )
        # --userns=keep-id is Podman-specific. Docker's rootless daemon already
        # maps container UIDs through its user namespace; retain fixed non-root
        # UID/GID and all other isolation flags.
        command.remove("--userns=keep-id")
        mount_index = command.index("--mount")
        command[mount_index + 1] = command[mount_index + 1].replace(",ro", ",readonly")
        # The harness executes as UID/GID 65532. Explicit ownership makes its
        # bounded scratch tmpfs writable without granting root or widening mounts.
        tmpfs_index = command.index("--tmpfs")
        command[tmpfs_index + 1] = "/work:rw,noexec,nosuid,nodev,size=100m,uid=65532,gid=65532,mode=700"
        return command

    def _runner_for_snapshot(self, snapshot: Path) -> "DockerIsolatedTestRunner":
        return DockerIsolatedTestRunner(
            snapshot,
            image=self.image,
            trusted_principal=self.trusted_principal,
            approve_run=self.approve_run,
            audit=self.audit,
            allowed_test_modules=self.allowed_test_modules,
            timeout_seconds=self.timeout_seconds,
            docker_binary=self.podman_binary,
            output_limit_bytes=self.output_limit_bytes,
            memory_limit=self.memory_limit,
            cpu_limit=self.cpu_limit,
            pids_limit=self.pids_limit,
        )
