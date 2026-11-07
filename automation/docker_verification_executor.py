"""Docker-isolated execution for trusted verification profiles.

This runner is deliberately separate from LocalVerificationExecutor: the latter
runs on the host and must never be described as a sandbox. Images must be pinned
by immutable sha256 digest and already present locally (no implicit image pulls).
"""
from __future__ import annotations

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
        self.commands = checked
        self.docker_binary = resolved_docker
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.max_output_bytes = max_output_bytes
        with self._connect() as db:
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
        for path in self.workspace.rglob("*"):
            if path.is_symlink():
                raise DockerVerificationError("sandbox_workspace_symlink_detected")
            if path.is_dir():
                continue
            if not path.is_file():
                raise DockerVerificationError("sandbox_workspace_non_regular_entry")
            relative = path.relative_to(self.workspace).as_posix()
            files[relative] = path.read_bytes()
        snapshot = RepositorySnapshotBuilder().build(files)
        if snapshot.snapshot_sha256 != self.expected_snapshot_sha256:
            raise DockerVerificationError("sandbox_workspace_snapshot_digest_mismatch")

    def _docker_argv(self, command: SandboxCommand) -> list[str]:
        return [
            self.docker_binary, "run", "--rm", "--pull=never", "--init",
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
        command = self.commands[command_id]
        command.validate()
        self._verify_workspace_snapshot()
        started = datetime.now(timezone.utc).isoformat()
        execution_id = uuid4().hex
        output = bytearray()
        lock = threading.Lock()
        limited = threading.Event()
        try:
            process = subprocess.Popen(
                self._docker_argv(command), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, close_fds=True,
                env={key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP") if key in os.environ},
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
                    try:
                        process.kill()
                    except OSError:
                        pass

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
            process.kill()
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
        with self._connect() as db:
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

    def get(self, execution_id: str) -> dict | None:
        if not isinstance(execution_id, str) or not re.fullmatch(r"[a-f0-9]{32}", execution_id):
            raise ValueError("invalid_execution_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM sandbox_verification_receipts WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        return dict(row) if row is not None else None
