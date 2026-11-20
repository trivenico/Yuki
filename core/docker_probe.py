"""Bounded read-only Docker diagnostics; results are never security attestation."""
from __future__ import annotations

import os
import selectors
import signal
import socket
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from core.sandbox_runtime import (
    DockerDaemonDiagnostics,
    SandboxRuntimeError,
    parse_docker_info,
)

_MAX_STDOUT = 262_144
_MAX_STDERR = 16_384
_MAX_TIMEOUT = 15.0


@dataclass(frozen=True)
class DockerProbePlan:
    """Validated command metadata, not a runtime probe or security attestation."""

    executable: str
    endpoint: str
    argv: tuple[str, ...]
    executed: bool = False
    attested: bool = False


@dataclass(frozen=True)
class DockerProbeResult:
    """Daemon self-reported diagnostics with explicit non-attestation status."""

    diagnostics: DockerDaemonDiagnostics
    exit_code: int
    executed: bool = True
    attested: bool = False


def build_docker_info_probe(executable: str, endpoint: str) -> DockerProbePlan:
    """Validate a Docker CLI and an explicit local Unix socket endpoint."""
    if os.name != "posix":
        raise SandboxRuntimeError("Docker CLI probe supports POSIX hosts only")
    if type(executable) is not str or not executable or not os.path.isabs(executable):
        raise SandboxRuntimeError("Docker CLI executable must be an absolute path")
    if "\x00" in executable:
        raise SandboxRuntimeError("Docker CLI executable path contains NUL")
    path = Path(executable)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SandboxRuntimeError("Docker CLI executable is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SandboxRuntimeError("Docker CLI path must be a regular non-symlink file")
    if not os.access(str(path), os.X_OK):
        raise SandboxRuntimeError("Docker CLI file is not executable")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxRuntimeError("Docker CLI path cannot be resolved safely") from exc
    if str(resolved) != executable:
        raise SandboxRuntimeError("Docker CLI path must be canonical")

    if type(endpoint) is not str or not endpoint or not os.path.isabs(endpoint) or "\x00" in endpoint:
        raise SandboxRuntimeError("Docker endpoint must be an absolute Unix socket path")
    socket_path = Path(endpoint)
    try:
        socket_info = socket_path.lstat()
    except OSError as exc:
        raise SandboxRuntimeError("Docker endpoint is unavailable") from exc
    if stat.S_ISLNK(socket_info.st_mode) or not stat.S_ISSOCK(socket_info.st_mode):
        raise SandboxRuntimeError("Docker endpoint must be a non-symlink Unix socket")
    argv = (str(resolved), "--host", "unix://" + endpoint, "info", "--format", "{{json .}}")
    return DockerProbePlan(str(resolved), endpoint, argv)


def run_docker_info_probe(
    plan: DockerProbePlan,
    *,
    timeout_seconds: float = 5.0,
) -> DockerProbeResult:
    """Run Docker info with bounded output/time and a scrubbed CLI environment.

    This is diagnostic only. The daemon controls the CLI reports are self-asserted;
    no container is started and no isolation property is attested.
    """
    if os.name != "posix":
        raise SandboxRuntimeError("bounded Docker probe supports POSIX hosts only")
    if not isinstance(plan, DockerProbePlan) or plan.executed or plan.attested:
        raise SandboxRuntimeError("probe plan is invalid or already marked as executed")
    if type(timeout_seconds) not in (int, float) or not 0.1 <= timeout_seconds <= _MAX_TIMEOUT:
        raise SandboxRuntimeError("probe timeout must be between 0.1 and 15 seconds")
    # Revalidate paths immediately before process creation to narrow replacement races.
    validated = build_docker_info_probe(plan.executable, plan.endpoint)
    if validated.argv != plan.argv:
        raise SandboxRuntimeError("probe plan changed after validation")

    stdout_data = bytearray()
    stderr_size = 0
    process = None
    with tempfile.TemporaryDirectory(prefix="yuki-docker-probe-") as private_home:
        env = {"PATH": os.defpath, "HOME": private_home, "DOCKER_CONFIG": private_home}
        try:
            process = subprocess.Popen(
                list(plan.argv), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=private_home, env=env, close_fds=True,
                start_new_session=True, bufsize=0,
            )
        except (OSError, ValueError) as exc:
            raise SandboxRuntimeError("Docker diagnostics process could not start") from exc

        selector = selectors.DefaultSelector()
        try:
            assert process.stdout is not None and process.stderr is not None
            for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)
            deadline = time.monotonic() + float(timeout_seconds)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxRuntimeError("Docker diagnostics probe timed out")
                events = selector.select(min(remaining, 0.1))
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        if len(stdout_data) + len(chunk) > _MAX_STDOUT:
                            raise SandboxRuntimeError("Docker diagnostics stdout exceeded 256 KiB")
                        stdout_data.extend(chunk)
                    else:
                        stderr_size += len(chunk)
                        if stderr_size > _MAX_STDERR:
                            raise SandboxRuntimeError("Docker diagnostics stderr exceeded 16 KiB")
            remaining = max(0.0, deadline - time.monotonic())
            try:
                exit_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise SandboxRuntimeError("Docker diagnostics probe timed out") from exc
            if exit_code != 0:
                raise SandboxRuntimeError("Docker diagnostics command failed")
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
            raise
        finally:
            selector.close()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    try:
        payload = bytes(stdout_data).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SandboxRuntimeError("Docker diagnostics output is not valid UTF-8") from exc
    diagnostics = parse_docker_info(payload)
    return DockerProbeResult(diagnostics=diagnostics, exit_code=exit_code)
