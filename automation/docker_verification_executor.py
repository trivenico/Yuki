"""Docker-isolated execution for trusted verification profiles.

This runner is deliberately separate from LocalVerificationExecutor: the latter
runs on the host and must never be described as a sandbox. Images must be pinned
by immutable sha256 digest and already present locally (no implicit image pulls).
"""
from __future__ import annotations

from contextlib import closing

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
from typing import Mapping
from uuid import uuid4

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_IMAGE = re.compile(r"^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")


def _stop_docker_execution(docker_binary: str, container_name: str, process) -> None:
    """Stop both the Docker CLI and its daemon-managed container."""
    try:
        process.kill()
    except (OSError, AttributeError):
        pass
    try:
        subprocess.run(
            [docker_binary, "rm", "--force", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class DockerVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxCommand:
    command_id: str
    argv: tuple[str, ...]
    timeout_seconds: int = 300

    def validate(self) -> None:
        if not isinstance(self.command_id, str) or not _NAME.fullmatch(self.command_id):
            raise ValueError("invalid_sandbox_command_id")
        if not isinstance(self.argv, tuple) or not self.argv or len(self.argv) > 32:
            raise ValueError("sandbox_argv_required")
        if any(not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > 4096 for arg in self.argv):
            raise ValueError("invalid_sandbox_argv")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 1800:
            raise ValueError("sandbox_timeout_out_of_range")


@dataclass(frozen=True)
class SandboxReceipt:
    execution_id: str
    command_id: str
    argv_json: str
    image: str
    artifact_sha256: str
    workspace: str
    started_at: str
    finished_at: str
    exit_code: int | None
    timed_out: bool
    output_limited: bool
    output_sha256: str
    output_bytes: int
    output_excerpt: str
    status: str


class DockerVerificationExecutor:
    """Run a configured command in a hardened, networkless Docker container."""

    def __init__(
        self,
        workspace: str | Path,
        database_path: str | Path,
        *,
        image: str,
        expected_snapshot_sha256: str,
        expected_artifact_sha256: str,
        commands: Mapping[str, SandboxCommand],
        docker_binary: str = "docker",
        memory_limit: str = "2g",
        cpu_limit: str = "2",
        pids_limit: int = 128,
        max_output_bytes: int = 256_000,
    ):
        raw_root = Path(workspace).expanduser()
        if raw_root.is_symlink():
            raise ValueError("sandbox_workspace_must_be_real_directory")
        root = raw_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("sandbox_workspace_must_be_real_directory")
        if not isinstance(image, str) or not _IMAGE.fullmatch(image):
            raise ValueError("sandbox_image_must_be_digest_pinned")
        if not isinstance(expected_snapshot_sha256, str) or not _SHA.fullmatch(expected_snapshot_sha256):
            raise ValueError("expected_snapshot_sha256_required")
        if not isinstance(expected_artifact_sha256, str) or not _SHA.fullmatch(expected_artifact_sha256):
            raise ValueError("expected_artifact_sha256_required")
        if not isinstance(docker_binary, str) or not docker_binary.strip():
            raise ValueError("docker_binary_required")
        if not isinstance(memory_limit, str) or not re.fullmatch(r"[1-9][0-9]*(?:m|g)", memory_limit):
            raise ValueError("invalid_memory_limit")
        if not isinstance(cpu_limit, str) or not re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", cpu_limit):
            raise ValueError("invalid_cpu_limit")
        if type(pids_limit) is not int or not 16 <= pids_limit <= 4096:
            raise ValueError("invalid_pids_limit")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 4_000_000:
            raise ValueError("invalid_output_limit")
        if not isinstance(commands, Mapping) or not commands:
            raise ValueError("sandbox_command_allowlist_required")
        checked = {}
        for key, command in commands.items():
            if not isinstance(command, SandboxCommand):
                raise TypeError("sandbox_allowlist_values_must_be_commands")
            command.validate()
            if key != command.command_id or key in checked:
                raise ValueError("sandbox_allowlist_key_mismatch")
            checked[key] = command
        resolved_docker = shutil.which(docker_binary)
        if resolved_docker is None:
            raise ValueError("docker_executable_not_found")
        self.workspace = root
        self.database_path = str(database_path)
        self.image = image
        self.expected_snapshot_sha256 = expected_snapshot_sha256
        self.expected_artifact_sha256 = expected_artifact_sha256
        self.commands = checked
        self.docker_binary = resolved_docker
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.max_output_bytes = max_output_bytes
        with closing(self._connect()) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS sandbox_verification_receipts (
                    execution_id TEXT PRIMARY KEY, command_id TEXT NOT NULL,
                    argv_json TEXT NOT NULL DEFAULT '[]',
                    image TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
                    workspace TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL, exit_code INTEGER,
                    timed_out INTEGER NOT NULL, output_limited INTEGER NOT NULL,
                    output_sha256 TEXT NOT NULL, output_bytes INTEGER NOT NULL,
                    output_excerpt TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                      ('passed','failed','timed_out','output_limited','launch_failed'))
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(sandbox_verification_receipts)")}
            if "argv_json" not in columns:
                db.execute("ALTER TABLE sandbox_verification_receipts ADD COLUMN argv_json TEXT NOT NULL DEFAULT '[]'")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _verify_workspace_snapshot(self) -> None:
        from automation.repository_snapshot import RepositorySnapshotBuilder

        files = {}
        total_bytes = 0
        max_files = 10_000
        max_file_bytes = 16_000_000
        max_total_bytes = 256_000_000
        for path in self.workspace.rglob("*"):
            if path.is_symlink():
                raise DockerVerificationError("sandbox_workspace_symlink_detected")
            if path.is_dir():
                continue
            if not path.is_file():
                raise DockerVerificationError("sandbox_workspace_non_regular_entry")
            if len(files) >= max_files:
                raise DockerVerificationError("sandbox_workspace_file_count_exceeded")
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise DockerVerificationError("sandbox_workspace_file_stat_failed") from exc
            if size > max_file_bytes:
                raise DockerVerificationError("sandbox_workspace_file_size_exceeded")
            if total_bytes + size > max_total_bytes:
                raise DockerVerificationError("sandbox_workspace_total_size_exceeded")
            relative = path.relative_to(self.workspace).as_posix()
            read_limit = min(max_file_bytes, max_total_bytes - total_bytes)
            try:
                with path.open("rb") as stream:
                    content = stream.read(read_limit + 1)
            except OSError as exc:
                raise DockerVerificationError("sandbox_workspace_file_read_failed") from exc
            if len(content) > max_file_bytes:
                raise DockerVerificationError("sandbox_workspace_file_size_exceeded")
            if len(content) > max_total_bytes - total_bytes:
                raise DockerVerificationError("sandbox_workspace_total_size_exceeded")
            if len(content) != size:
                raise DockerVerificationError("sandbox_workspace_changed_during_snapshot")
            total_bytes += len(content)
            files[relative] = content
        snapshot = RepositorySnapshotBuilder(
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        ).build(files)
        if snapshot.snapshot_sha256 != self.expected_snapshot_sha256:
            raise DockerVerificationError("sandbox_workspace_snapshot_digest_mismatch")

    def _docker_argv(self, command: SandboxCommand, container_name: str) -> list[str]:
        return [
            self.docker_binary, "run", "--rm", "--pull=never", "--init",
            "--name", container_name,
            "--network=none", "--read-only", "--user=65532:65532",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--security-opt=seccomp=default",
            f"--pids-limit={self.pids_limit}", f"--memory={self.memory_limit}",
            f"--memory-swap={self.memory_limit}", f"--cpus={self.cpu_limit}",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--mount", f"type=bind,src={self.workspace},dst=/workspace,readonly",
            "--workdir=/workspace",
            "--env=HOME=/tmp", "--env=TMPDIR=/tmp",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            self.image, *command.argv,
        ]

    def run(self, command_id: str, *, artifact_sha256: str) -> SandboxReceipt:
        if not isinstance(command_id, str) or command_id not in self.commands:
            raise PermissionError("sandbox_command_not_allowlisted")
        if not isinstance(artifact_sha256, str) or not _SHA.fullmatch(artifact_sha256):
            raise ValueError("artifact_sha256_required")
        if artifact_sha256 != self.expected_artifact_sha256:
            raise DockerVerificationError("artifact_digest_does_not_match_configured_workspace")
        command = self.commands[command_id]
        command.validate()
        self._verify_workspace_snapshot()
        started = datetime.now(timezone.utc).isoformat()
        execution_id = uuid4().hex
        output = bytearray()
        lock = threading.Lock()
        limited = threading.Event()
        container_name = "yuki-verification-" + execution_id
        try:
            process = subprocess.Popen(
                self._docker_argv(command, container_name), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, close_fds=True,
                env={key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP") if key in os.environ},
                start_new_session=(os.name != "nt"),
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
        except OSError:
            return self._persist(SandboxReceipt(
                execution_id, command_id, json.dumps(command.argv), self.image, artifact_sha256,
                str(self.workspace), started, datetime.now(timezone.utc).isoformat(),
                None, False, False, hashlib.sha256(b"").hexdigest(), 0, "", "launch_failed",
            ))

        def drain(stream):
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with lock:
                    remaining = self.max_output_bytes - len(output)
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        limited.set()
                if limited.is_set():
                    _stop_docker_execution(self.docker_binary, container_name, process)

        readers = [
            threading.Thread(target=drain, args=(process.stdout,), daemon=True),
            threading.Thread(target=drain, args=(process.stderr,), daemon=True),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            process.wait(timeout=command.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop_docker_execution(self.docker_binary, container_name, process)
            process.wait()
        for reader in readers:
            reader.join(timeout=2)
        captured = bytes(output)
        status = "timed_out" if timed_out else (
            "output_limited" if limited.is_set() else (
                "passed" if process.returncode == 0 else "failed"
            )
        )
        return self._persist(SandboxReceipt(
            execution_id, command_id, json.dumps(command.argv), self.image, artifact_sha256,
            str(self.workspace), started, datetime.now(timezone.utc).isoformat(),
            process.returncode, timed_out, limited.is_set(),
            hashlib.sha256(captured).hexdigest(), len(captured),
            captured[:4000].decode("utf-8", errors="replace"), status,
        ))

    def _persist(self, receipt: SandboxReceipt) -> SandboxReceipt:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO sandbox_verification_receipts(
                    execution_id, command_id, argv_json, image, artifact_sha256,
                    workspace, started_at, finished_at, exit_code, timed_out,
                    output_limited, output_sha256, output_bytes, output_excerpt, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt.execution_id, receipt.command_id, receipt.argv_json, receipt.image,
                    receipt.artifact_sha256, receipt.workspace, receipt.started_at,
                    receipt.finished_at, receipt.exit_code, int(receipt.timed_out),
                    int(receipt.output_limited), receipt.output_sha256,
                    receipt.output_bytes, receipt.output_excerpt, receipt.status,
                ),
            )
        return receipt

    def validate_evidence_reference(
        self, reference: str, *, artifact_sha256: str, check_name: str,
    ) -> bool:
        prefix = "yuki-verification-execution://"
        if not isinstance(reference, str) or not reference.startswith(prefix):
            return False
        expected_commands = {"tests": "unit-tests"}
        expected_command = expected_commands.get(check_name)
        if expected_command is None:
            return False
        try:
            stored = self.get(reference[len(prefix):])
        except ValueError:
            return False
        if stored is None:
            return False
        configured = self.commands.get(expected_command)
        return bool(
            configured is not None
            and stored["command_id"] == expected_command
            and json.loads(stored["argv_json"]) == list(configured.argv)
            and stored["image"] == self.image
            and stored["artifact_sha256"] == artifact_sha256
            and stored["status"] == "passed"
            and stored["exit_code"] == 0
            and not stored["timed_out"]
            and not stored["output_limited"]
        )

    def list_for_artifact(self, artifact_sha256: str, *, limit: int = 20) -> tuple[dict, ...]:
        """Return bounded execution receipt metadata for one exact artifact."""
        if not isinstance(artifact_sha256, str) or not _SHA.fullmatch(artifact_sha256):
            raise ValueError("artifact_sha256_required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit_must_be_between_1_and_100")
        with closing(self._connect()) as db, db:
            rows = db.execute(
                """SELECT execution_id, command_id, argv_json, image, artifact_sha256,
                          started_at, finished_at, exit_code, timed_out, output_limited,
                          output_sha256, output_bytes, status
                   FROM sandbox_verification_receipts
                   WHERE artifact_sha256=?
                   ORDER BY started_at DESC, execution_id LIMIT ?""",
                (artifact_sha256, limit),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def get(self, execution_id: str) -> dict | None:
        if not isinstance(execution_id, str) or not re.fullmatch(r"[a-f0-9]{32}", execution_id):
            raise ValueError("invalid_execution_id")
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM sandbox_verification_receipts WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        return dict(row) if row is not None else None
