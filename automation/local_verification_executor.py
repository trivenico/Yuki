"""Allow-listed, bounded local command execution for verification evidence.

No shell is used. Commands are configured by trusted application code; callers
cannot supply argv. Output is drained concurrently and the child is terminated
when the combined output cap is exceeded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
from typing import Mapping, Sequence
from uuid import uuid4

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class VerificationExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerificationCommand:
    command_id: str
    argv: tuple[str, ...]
    timeout_seconds: float = 120.0

    def validate(self) -> None:
        if not isinstance(self.command_id, str) or not _NAME.fullmatch(self.command_id):
            raise ValueError("invalid_verification_command_id")
        if not isinstance(self.argv, tuple) or not self.argv or len(self.argv) > 32:
            raise ValueError("verification_argv_required")
        if any(not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > 4096 for arg in self.argv):
            raise ValueError("invalid_verification_argv")
        if type(self.timeout_seconds) not in (int, float) or not 0.1 <= self.timeout_seconds <= 900:
            raise ValueError("verification_timeout_out_of_range")


@dataclass(frozen=True)
class VerificationExecution:
    execution_id: str
    command_id: str
    argv_json: str
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


class LocalVerificationExecutor:
    """Run only configured commands and persist immutable execution receipts."""

    def __init__(
        self,
        workspace: str | Path,
        database_path: str | Path,
        commands: Mapping[str, VerificationCommand],
        *,
        max_output_bytes: int = 256_000,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError("verification_workspace_must_exist")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 4_000_000:
            raise ValueError("max_output_bytes_out_of_range")
        if not isinstance(commands, Mapping) or not commands:
            raise ValueError("verification_command_allowlist_required")
        normalized = {}
        for key, command in commands.items():
            if not isinstance(command, VerificationCommand):
                raise TypeError("allowlist values must be VerificationCommand")
            command.validate()
            if key != command.command_id or key in normalized:
                raise ValueError("verification_command_allowlist_key_mismatch")
            normalized[key] = command
        self.commands = normalized
        self.max_output_bytes = max_output_bytes
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path_required")
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS verification_executions (
                    execution_id TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL,
                    argv_json TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    exit_code INTEGER,
                    timed_out INTEGER NOT NULL,
                    output_limited INTEGER NOT NULL,
                    output_sha256 TEXT NOT NULL,
                    output_bytes INTEGER NOT NULL,
                    output_excerpt TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('passed','failed','timed_out','output_limited','launch_failed'))
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def run(self, command_id: str) -> VerificationExecution:
        if not isinstance(command_id, str) or command_id not in self.commands:
            raise PermissionError("verification_command_not_allowlisted")
        command = self.commands[command_id]
        command.validate()
        started = datetime.now(timezone.utc).isoformat()
        execution_id = uuid4().hex
        output = bytearray()
        output_lock = threading.Lock()
        output_limited = threading.Event()

        # Only a small, explicit environment is inherited by the child.
        env = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP")
            if key in os.environ
        }

        try:
            process = subprocess.Popen(
                list(command.argv),
                cwd=str(self.workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
            )
        except OSError:
            finished = datetime.now(timezone.utc).isoformat()
            receipt = VerificationExecution(
                execution_id, command_id, json.dumps(command.argv), str(self.workspace),
                started, finished, None, False, False, hashlib.sha256(b"").hexdigest(),
                0, "", "launch_failed",
            )
            self._persist(receipt)
            return receipt

        def drain(stream):
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                with output_lock:
                    remaining = self.max_output_bytes - len(output)
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        output_limited.set()
                if output_limited.is_set():
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
        finished = datetime.now(timezone.utc).isoformat()
        captured = bytes(output)
        exit_code = process.returncode
        if timed_out:
            status = "timed_out"
        elif output_limited.is_set():
            status = "output_limited"
        elif exit_code == 0:
            status = "passed"
        else:
            status = "failed"
        receipt = VerificationExecution(
            execution_id=execution_id,
            command_id=command_id,
            argv_json=json.dumps(command.argv, separators=(",", ":")),
            workspace=str(self.workspace),
            started_at=started,
            finished_at=finished,
            exit_code=exit_code,
            timed_out=timed_out,
            output_limited=output_limited.is_set(),
            output_sha256=hashlib.sha256(captured).hexdigest(),
            output_bytes=len(captured),
            output_excerpt=captured[: min(len(captured), 4000)].decode("utf-8", errors="replace"),
            status=status,
        )
        self._persist(receipt)
        return receipt

    def _persist(self, receipt: VerificationExecution) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO verification_executions VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt.execution_id, receipt.command_id, receipt.argv_json,
                    receipt.workspace, receipt.started_at, receipt.finished_at,
                    receipt.exit_code, int(receipt.timed_out), int(receipt.output_limited),
                    receipt.output_sha256, receipt.output_bytes, receipt.output_excerpt,
                    receipt.status,
                ),
            )

    def get(self, execution_id: str) -> dict | None:
        if not isinstance(execution_id, str) or not re.fullmatch(r"[a-f0-9]{32}", execution_id):
            raise ValueError("invalid_execution_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM verification_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def evidence_reference(receipt: VerificationExecution) -> str:
        """Stable machine-readable pointer to the durable execution receipt."""
        return "yuki-verification-execution://" + receipt.execution_id
