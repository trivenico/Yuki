"""Docker-isolated reviewer execution; host credentials never enter the container.

The configured image must be digest-pinned and already cached locally. The
container receives a bounded JSON request on stdin and must emit one structured
ReviewResult JSON object on stdout. It receives no host mounts, network, session
token, signing key, or ledger handle.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Mapping

from automation.reviewer_contracts import (
    ReviewFinding, ReviewResult, ReviewTask, build_review_result,
)

_IMAGE = re.compile(r"^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$")


class DockerReviewerExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewerContainerCommand:
    argv: tuple[str, ...]
    timeout_seconds: int = 180

    def validate(self) -> None:
        if not isinstance(self.argv, tuple) or not self.argv or len(self.argv) > 32:
            raise ValueError("reviewer_container_argv_required")
        if any(not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > 4096 for arg in self.argv):
            raise ValueError("invalid_reviewer_container_argv")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 900:
            raise ValueError("reviewer_container_timeout_out_of_range")


class DockerReviewerExecutor:
    """Callable executor compatible with ReviewerExecutionAdapter."""

    def __init__(
        self,
        *,
        image: str,
        command: ReviewerContainerCommand,
        docker_binary: str = "docker",
        memory_limit: str = "1g",
        cpu_limit: str = "1",
        pids_limit: int = 64,
        max_input_bytes: int = 5_000_000,
        max_output_bytes: int = 256_000,
    ):
        if not isinstance(image, str) or not _IMAGE.fullmatch(image):
            raise ValueError("reviewer_image_must_be_digest_pinned")
        if not isinstance(command, ReviewerContainerCommand):
            raise TypeError("reviewer_container_command_required")
        command.validate()
        if not isinstance(docker_binary, str) or not docker_binary.strip():
            raise ValueError("docker_binary_required")
        if not re.fullmatch(r"[1-9][0-9]*(?:m|g)", memory_limit):
            raise ValueError("invalid_reviewer_memory_limit")
        if not re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", cpu_limit):
            raise ValueError("invalid_reviewer_cpu_limit")
        if type(pids_limit) is not int or not 16 <= pids_limit <= 1024:
            raise ValueError("invalid_reviewer_pids_limit")
        if type(max_input_bytes) is not int or not 1024 <= max_input_bytes <= 20_000_000:
            raise ValueError("invalid_reviewer_input_limit")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 2_000_000:
            raise ValueError("invalid_reviewer_output_limit")
        resolved = shutil.which(docker_binary)
        if resolved is None:
            raise ValueError("docker_executable_not_found")
        self.image = image
        self.command = command
        self.docker_binary = resolved
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.max_input_bytes = max_input_bytes
        self.max_output_bytes = max_output_bytes

    @staticmethod
    def _request(task: ReviewTask, evidence: Mapping[str, bytes]) -> bytes:
        task.validate()
        if set(evidence) != set(task.evidence_references):
            raise DockerReviewerExecutionError("reviewer_evidence_scope_mismatch")
        encoded = {}
        for reference in task.evidence_references:
            content = evidence[reference]
            if not isinstance(content, bytes):
                raise DockerReviewerExecutionError("reviewer_evidence_must_be_bytes")
            encoded[reference] = base64.b64encode(content).decode("ascii")
        payload = {
            "protocol_version": 1,
            "task": {
                "task_id": task.task_id,
                "artifact_sha256": task.artifact_sha256,
                "author_agent_id": task.author_agent_id,
                "reviewer_agent_id": task.reviewer_agent_id,
                "check_name": task.check_name,
                "requirements": list(task.requirements),
                "evidence_references": list(task.evidence_references),
            },
            "evidence_base64": encoded,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _parse_result(raw: bytes, task: ReviewTask) -> ReviewResult:
        try:
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict) or set(value) != {"status", "summary", "findings"}:
                raise ValueError("unexpected_result_fields")
            if not isinstance(value["findings"], list):
                raise ValueError("findings_must_be_list")
            findings = []
            for item in value["findings"]:
                if not isinstance(item, dict):
                    raise ValueError("finding_must_be_object")
                if set(item) - {"finding_id", "severity", "state", "title", "detail", "evidence_reference", "recommendation"}:
                    raise ValueError("unexpected_finding_fields")
                findings.append(ReviewFinding(**item))
            return build_review_result(
                task, status=value["status"], summary=value["summary"], findings=findings,
            )
        except Exception as exc:
            raise DockerReviewerExecutionError("reviewer_container_returned_invalid_result") from exc

    def _argv(self, cidfile: str) -> list[str]:
        return [
            self.docker_binary, "run", "--rm", "--pull=never", "--init", "-i",
            "--cidfile", cidfile,
            "--network=none", "--read-only", "--user=65532:65532",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--security-opt=seccomp=default",
            f"--pids-limit={self.pids_limit}", f"--memory={self.memory_limit}",
            f"--memory-swap={self.memory_limit}", f"--cpus={self.cpu_limit}",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m",
            "--env=HOME=/tmp", "--env=TMPDIR=/tmp",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            self.image, *self.command.argv,
        ]

    def _cleanup_container(self, cidfile: str) -> None:
        try:
            container_id = Path(cidfile).read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return
        if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            return
        for action in ("kill", "rm", "-f"):
            try:
                subprocess.run(
                    [self.docker_binary, action, container_id],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, shell=False, close_fds=True,
                    timeout=5, env={"PATH": os.path.dirname(self.docker_binary)},
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            if action == "kill":
                break

    def __call__(self, task: ReviewTask, evidence: Mapping[str, bytes]) -> ReviewResult:
        payload = self._request(task, evidence)
        if len(payload) > self.max_input_bytes:
            raise DockerReviewerExecutionError("reviewer_input_exceeds_size_limit")
        stdout = bytearray()
        stderr = bytearray()
        output_lock = threading.Lock()
        output_limited = threading.Event()
        process = None
        with tempfile.TemporaryDirectory(prefix="yuki-review-") as temp:
            cidfile = str(Path(temp) / "container.cid")
            try:
                process = subprocess.Popen(
                    self._argv(cidfile), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    shell=False, close_fds=True,
                    env={key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP") if key in os.environ},
                )
            except OSError as exc:
                raise DockerReviewerExecutionError("reviewer_container_launch_failed") from exc

            def drain(stream, target):
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    with output_lock:
                        remaining = self.max_output_bytes - len(stdout) - len(stderr)
                        if remaining > 0:
                            target.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            output_limited.set()
                    if output_limited.is_set():
                        self._cleanup_container(cidfile)
                        try:
                            process.kill()
                        except OSError:
                            pass

            readers = [
                threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
                threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
            ]
            for reader in readers:
                reader.start()

            def send_input():
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

            writer = threading.Thread(target=send_input, daemon=True)
            writer.start()
            timed_out = False
            try:
                process.wait(timeout=self.command.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._cleanup_container(cidfile)
                process.kill()
                process.wait()
            writer.join(timeout=2)
            for reader in readers:
                reader.join(timeout=2)
            if timed_out:
                self._cleanup_container(cidfile)
                raise DockerReviewerExecutionError("reviewer_container_timed_out")
            if output_limited.is_set():
                self._cleanup_container(cidfile)
                raise DockerReviewerExecutionError("reviewer_container_output_limit_exceeded")
            if process.returncode != 0:
                raise DockerReviewerExecutionError("reviewer_container_failed")
            return self._parse_result(bytes(stdout), task)


__all__ = [
    "DockerReviewerExecutor", "DockerReviewerExecutionError",
    "ReviewerContainerCommand",
]
