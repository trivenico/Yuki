"""One-shot Docker launcher for approved skill-container requests.

This launcher is deliberately not wired into DurableSkillWorker. It accepts
only the immutable request contract and a bounded, digest-verified JSON input.
It requires a local image pinned by digest and never pulls images at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from core.skill_container_request import (
    SkillContainerRequest,
    SkillContainerRequestError,
    build_skill_runtime_input,
)


class SkillContainerLaunchError(RuntimeError):
    """The isolated skill container could not be safely launched or cleaned up."""


@dataclass(frozen=True)
class SkillContainerResult:
    returncode: int
    stdout: bytes
    timed_out: bool
    output_truncated: bool
    duration_seconds: float


_IMAGE_DIGEST = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,180}@sha256:[0-9a-f]{64}$"
)
_CONTAINER_ID = re.compile(r"^yuki-skill-[0-9a-f]{24}$")


def _docker_env() -> dict[str, str]:
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
        "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def build_skill_docker_argv(
    request: SkillContainerRequest, *, container_name: str
) -> list[str]:
    if not isinstance(request, SkillContainerRequest):
        raise SkillContainerLaunchError("validated skill container request required")
    if not _IMAGE_DIGEST.fullmatch(request.image):
        raise SkillContainerLaunchError("runtime image must be pinned by sha256 digest")
    if not _CONTAINER_ID.fullmatch(container_name):
        raise SkillContainerLaunchError("invalid generated container name")
    if request.network_mode != "none" or not all((
        request.mount_read_only, request.read_only_root, request.non_root,
        request.drop_all_capabilities, request.no_new_privileges,
    )):
        raise SkillContainerLaunchError("skill runtime security policy is not restrictive")
    if request.mount_target != "/yuki/skill":
        raise SkillContainerLaunchError("unsupported skill mount target")
    if request.command != ("/opt/yuki/bin/skill-runner", "--protocol-v1"):
        raise SkillContainerLaunchError("skill runtime command must be fixed protocol launcher")
    root = Path(request.workspace)
    if not root.is_absolute() or root.is_symlink():
        raise SkillContainerLaunchError("staging workspace must be an absolute non-symlink path")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise SkillContainerLaunchError("staging workspace is unavailable") from exc
    if not resolved.is_dir() or str(resolved) != request.workspace:
        raise SkillContainerLaunchError("staging workspace must be canonical directory")
    integer_limits = (
        request.timeout_seconds, request.memory_mb, request.cpu_millis,
        request.max_processes, request.max_input_bytes, request.max_output_bytes,
    )
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in integer_limits):
        raise SkillContainerLaunchError("invalid resource limit")
    if request.timeout_seconds > 3600 or request.memory_mb > 65536 or request.cpu_millis > 64000:
        raise SkillContainerLaunchError("resource limit exceeds launcher maximum")
    if request.max_processes > 1024 or request.max_input_bytes > 1_048_576 or request.max_output_bytes > 1_048_576:
        raise SkillContainerLaunchError("protocol or process limit exceeds launcher maximum")
    return [
        "docker", "run", "--interactive", "--init", "--pull=never",
        "--name", container_name, "--network", "none", "--ipc", "private",
        "--read-only", "--user", "65532:65532", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--memory", f"{request.memory_mb}m",
        "--cpus", f"{request.cpu_millis / 1000:.3f}",
        "--pids-limit", str(request.max_processes),
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={min(request.memory_mb, 64)}m",
        "--mount", f"type=bind,src={request.workspace},dst=/yuki/skill,readonly",
        "--workdir", "/yuki/skill", request.image, *request.command,
    ]


def _remove_container(name: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "rm", "--force", name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, shell=False, env=_docker_env(),
            timeout=10, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def launch_skill_container(
    request: SkillContainerRequest, arguments: object
) -> SkillContainerResult:
    """Launch exactly one approved invocation; no host-execution fallback exists."""
    try:
        payload = build_skill_runtime_input(
            _binding_view(request), arguments, max_input_bytes=request.max_input_bytes
        )
    except (SkillContainerRequestError, AttributeError, TypeError, ValueError) as exc:
        raise SkillContainerLaunchError("runtime input does not match approved request") from exc
    name = "yuki-skill-" + secrets.token_hex(12)
    argv = build_skill_docker_argv(request, container_name=name)
    output = bytearray()
    output_limit_hit = threading.Event()
    writer_errors: list[BaseException] = []
    reader_errors: list[BaseException] = []
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, shell=False, close_fds=True, env=_docker_env(),
        )
    except OSError as exc:
        raise SkillContainerLaunchError("Docker runtime unavailable; skill execution denied") from exc

    def write_input() -> None:
        try:
            if process.stdin is not None:
                process.stdin.write(payload)
                process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            writer_errors.append(exc)

    def read_output() -> None:
        try:
            stream = process.stdout
            if stream is None:
                return
            while True:
                chunk = stream.read(min(8192, request.max_output_bytes + 1))
                if not chunk:
                    return
                remaining = request.max_output_bytes - len(output)
                if len(chunk) > remaining:
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    output_limit_hit.set()
                    process.kill()
                    return
                output.extend(chunk)
        except (OSError, ValueError) as exc:
            reader_errors.append(exc)

    writer = threading.Thread(target=write_input, name="yuki-skill-input", daemon=True)
    reader = threading.Thread(target=read_output, name="yuki-skill-output", daemon=True)
    writer.start()
    reader.start()
    timed_out = False
    returncode = -1
    try:
        try:
            returncode = process.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise SkillContainerLaunchError("skill container CLI did not stop after timeout") from exc
    finally:
        writer.join(timeout=2)
        reader.join(timeout=2)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if not _remove_container(name):
            raise SkillContainerLaunchError("skill container cleanup could not be confirmed")
    if reader.is_alive() or writer.is_alive() or reader_errors:
        raise SkillContainerLaunchError("skill runtime pipe handling did not complete safely")
    # Broken pipe is expected only if the container rejected input; nonzero exit
    # remains visible to the supervisor and must not be interpreted as success.
    return SkillContainerResult(
        returncode=int(returncode), stdout=bytes(output), timed_out=timed_out,
        output_truncated=output_limit_hit.is_set(),
        duration_seconds=max(0.0, time.monotonic() - started),
    )


def _binding_view(request: SkillContainerRequest):
    """Minimal adapter to reuse the existing canonical input digest verifier."""
    class BindingView:
        argument_sha256 = request.argument_sha256
    return BindingView()
