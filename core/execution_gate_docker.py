"""Build the create-only Docker command for the image-owned execution gate.

This module does not start containers or release task authorization. It keeps
the Docker CLI command boundary explicit so the gate is the initial process,
not a post-start callback layered over a task command.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence

from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import build_docker_argv


GATE_ENTRYPOINT = "/usr/local/bin/yuki-execution-gate"
GATE_SOCKET_DIRECTORY = "/run/yuki-gate"
_CONTAINER_NAME = re.compile(r"^yuki-[0-9a-f]{32}$")


def build_gate_container_create_argv(
    *,
    docker_binary: str,
    policy: SandboxPolicy,
    image: str,
    workspace: str,
    gate_arguments: Sequence[str],
    private_gate_directory: str,
    container_name: str,
    apparmor_profile: str,
    container_labels: dict[str, str],
) -> list[str]:
    """Return a Docker create argv whose only initial process is the gate.

    private_gate_directory must be operator-provisioned with permissions
    validated for the actual Docker user-namespace mapping. This builder
    cannot establish that mapping.
    """
    if (
        type(docker_binary) is not str or not os.path.isabs(docker_binary)
        or os.path.islink(docker_binary) or not os.path.isfile(docker_binary)
        or not os.access(docker_binary, os.X_OK)
    ):
        raise ValueError("docker_binary must be an absolute non-symlink executable")
    if type(private_gate_directory) is not str or not os.path.isabs(private_gate_directory):
        raise ValueError("private_gate_directory must be an absolute path")
    directory = Path(private_gate_directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("private gate directory must be an existing non-symlink directory")
    try:
        resolved_directory = directory.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("private gate directory cannot be resolved") from exc
    if not hasattr(os, "getuid") or resolved_directory.stat().st_uid != os.getuid():
        raise ValueError("private gate directory must be owned by the non-root executor")
    if resolved_directory.stat().st_mode & 0o022:
        raise ValueError("private gate directory must not be group/world writable")
    if resolved_directory == Path(resolved_directory.anchor) or any(
        char in str(resolved_directory) for char in (",", "\n", "\r")
    ):
        raise ValueError("private gate directory path is unsafe for Docker mount syntax")
    if type(container_name) is not str or not _CONTAINER_NAME.fullmatch(container_name):
        raise ValueError("container_name must be a generated Yuki container name")
    if type(apparmor_profile) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", apparmor_profile):
        raise ValueError("apparmor_profile must be a bounded profile identifier")
    if type(gate_arguments) not in (tuple, list) or not gate_arguments or len(gate_arguments) > 32:
        raise ValueError("gate_arguments must be a bounded argument sequence")
    if any(type(arg) is not str or not arg or len(arg) > 4096 or "\x00" in arg for arg in gate_arguments):
        raise ValueError("gate arguments must be bounded NUL-free strings")
    if any(arg == "--entrypoint" for arg in gate_arguments):
        raise ValueError("gate arguments cannot override the fixed entrypoint")
    required_labels = {
        "yuki.managed_by", "yuki.task_id", "yuki.worker_id",
        "yuki.owner_sha256", "yuki.profile_id", "yuki.image_digest",
    }
    if (
        type(container_labels) is not dict or set(container_labels) != required_labels
        or any(
            type(key) is not str or type(value) is not str or not value
            or len(value) > 200 or "\\x00" in value
            for key, value in container_labels.items()
        )
        or container_labels.get("yuki.managed_by") != "yuki"
        or not re.fullmatch(r"[0-9a-f]{64}", container_labels.get("yuki.owner_sha256", ""))
        or not re.fullmatch(r"[0-9a-f]{64}", container_labels.get("yuki.image_digest", ""))
        or container_labels.get("yuki.image_digest") != image.rsplit("@", 1)[-1]
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", container_labels.get("yuki.profile_id", ""))
    ):
        raise ValueError("container_labels must be the complete validated Yuki ownership label set")

    argv = build_docker_argv(
        policy, image=image, workspace=workspace, command=list(gate_arguments),
    )
    argv[1] = "create"
    # The gate itself must be PID 1 so its exec replaces the gate process.
    argv = [item for item in argv if item != "--init"]
    image_index = argv.index(image)
    argv[image_index:image_index] = [
        "--name", container_name,
        "--security-opt", f"apparmor={apparmor_profile}",
        "--entrypoint", GATE_ENTRYPOINT,
        "--mount",
        f"type=bind,src={resolved_directory},dst={GATE_SOCKET_DIRECTORY},rw",
    ]
    for key in sorted(container_labels):
        value = container_labels[key]
        if (
            type(key) is not str or type(value) is not str or not key or not value
            or len(key) > 128 or len(value) > 200 or "\x00" in key or "\x00" in value
        ):
            raise ValueError("container labels contain invalid values")
        argv[2:2] = ["--label", f"{key}={value}"]
    argv[0] = docker_binary
    return argv
