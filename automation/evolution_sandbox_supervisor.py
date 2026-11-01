"""Supervise bounded Docker executions for approved evolution work.

Run this service only on a dedicated worker with a rootless container runtime
or a disposable VM boundary. Access to a privileged Docker daemon is
root-equivalent and must never be exposed to agent-controlled processes.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable, Sequence

from automation.evolution_execution_gate import SandboxExecutionSpec
from core.sandbox_policy import SandboxPolicy
from core.sandbox_runtime import build_docker_argv


_PINNED_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,200}@sha256:[a-f0-9]{64}$")


class EvolutionSandboxError(RuntimeError):
    """Raised when a sandbox cannot be started or supervised safely."""


@dataclass(frozen=True)
class SandboxExecutionResult:
    plan_id: str
    request_id: str
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    output_limit_exceeded: bool
    stdout: str
    stderr: str
    duration_ms: int


class EvolutionSandboxSupervisor:
    """Run a prepared execution spec with time and combined-output limits."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        docker_binary: str = "docker",
        popen_factory: Callable = subprocess.Popen,
        argv_builder: Callable = build_docker_argv,
        poll_interval: float = 0.05,
    ):
        root = Path(workspace_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace_root must be an existing directory.")
        if not isinstance(docker_binary, str) or not docker_binary or os.path.basename(docker_binary) != docker_binary:
            raise ValueError("docker_binary must be a command name, not a path.")
        if not 0.01 <= poll_interval <= 1:
            raise ValueError("poll_interval must be between 0.01 and 1 second.")
        self.workspace_root = root
        self.docker_binary = docker_binary
        self.popen_factory = popen_factory
        self.argv_builder = argv_builder
        self.poll_interval = poll_interval

    def _validate_spec(self, spec: SandboxExecutionSpec) -> Path:
        if not isinstance(spec, SandboxExecutionSpec):
            raise EvolutionSandboxError("a prepared SandboxExecutionSpec is required.")
        if not _PINNED_IMAGE.fullmatch(spec.image):
            raise EvolutionSandboxError("container image must be pinned by sha256 digest.")
        if not isinstance(spec.policy, SandboxPolicy):
            raise EvolutionSandboxError("invalid sandbox policy.")
        try:
            spec.policy.validate()
        except Exception as exc:
            raise EvolutionSandboxError("sandbox policy validation failed.") from exc
        if spec.policy.network_mode != "none":
            raise EvolutionSandboxError("networking must be disabled.")
        if "write_workspace" not in spec.policy.capabilities:
            raise EvolutionSandboxError("workspace write capability is required.")
        try:
            workspace = Path(spec.workspace).resolve(strict=True)
            if not workspace.is_dir() or workspace == self.workspace_root or not workspace.is_relative_to(self.workspace_root):
                raise EvolutionSandboxError("workspace is outside the configured root.")
        except (OSError, RuntimeError, TypeError) as exc:
            raise EvolutionSandboxError("workspace cannot be resolved safely.") from exc
        if not isinstance(spec.command, tuple) or not spec.command or len(spec.command) > 64:
            raise EvolutionSandboxError("command must be a bounded argument tuple.")
        if any(not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > 4096 for arg in spec.command):
            raise EvolutionSandboxError("command contains an invalid argument.")
        return workspace

    @staticmethod
    def _capture(stream, sink: bytearray, state: dict, lock: threading.Lock, limit: int) -> None:
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, limit - state["total"])
                    accepted = chunk[:remaining]
                    if accepted:
                        sink.extend(accepted)
                        state["total"] += len(accepted)
                    if len(accepted) != len(chunk):
                        state["exceeded"] = True
        finally:
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
            process.wait(timeout=2)

    def _stop_container(self, cidfile: Path) -> None:
        try:
            container_id = cidfile.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return
        if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            return
        try:
            subprocess.run(
                [self.docker_binary, "stop", "--time", "1", container_id],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def execute(self, spec: SandboxExecutionSpec, *, cancel_event: threading.Event | None = None) -> SandboxExecutionResult:
        workspace = self._validate_spec(spec)
        policy = spec.policy
        try:
            argv = self.argv_builder(
                policy, image=spec.image, workspace=str(workspace), command=spec.command,
            )
        except Exception as exc:
            raise EvolutionSandboxError("sandbox command construction failed.") from exc
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
            raise EvolutionSandboxError("sandbox command builder returned invalid argv.")

        started = time.monotonic()
        stdout = bytearray()
        stderr = bytearray()
        capture_state = {"total": 0, "exceeded": False}
        capture_lock = threading.Lock()
        timed_out = cancelled = False

        with tempfile.TemporaryDirectory(prefix="yuki-evo-") as temp_dir:
            cidfile = Path(temp_dir) / "container.cid"
            if argv[0] == "docker":
                if argv[1:2] != ["run"]:
                    raise EvolutionSandboxError("Docker backend must invoke 'docker run'.")
                argv = [argv[0], argv[1], "--pull=never", "--cidfile", str(cidfile), *argv[2:]]
            popen_kwargs = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "bufsize": 0,
            }
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            elif os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                process = self.popen_factory(argv, **popen_kwargs)
            except OSError as exc:
                raise EvolutionSandboxError("sandbox process could not be started.") from exc

            out_thread = threading.Thread(
                target=self._capture, args=(process.stdout, stdout, capture_state, capture_lock, policy.max_output_bytes),
                daemon=True,
            )
            err_thread = threading.Thread(
                target=self._capture, args=(process.stderr, stderr, capture_state, capture_lock, policy.max_output_bytes),
                daemon=True,
            )
            out_thread.start()
            err_thread.start()
            deadline = started + policy.timeout_seconds
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    self._terminate(process)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._terminate(process)
                    break
                with capture_lock:
                    exceeded = capture_state["exceeded"]
                if exceeded:
                    self._terminate(process)
                    break
                time.sleep(self.poll_interval)

            try:
                exit_code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                exit_code = process.poll()
            out_thread.join(timeout=2)
            err_thread.join(timeout=2)
            with capture_lock:
                output_limit_exceeded = capture_state["exceeded"]
            if timed_out or cancelled or output_limit_exceeded:
                self._stop_container(cidfile)

        return SandboxExecutionResult(
            plan_id=spec.plan_id,
            request_id=spec.request_id,
            exit_code=exit_code,
            timed_out=timed_out,
            cancelled=cancelled,
            output_limit_exceeded=output_limit_exceeded,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
