"""Durable SQLite registry for sandbox job recovery metadata.

This store persists control-plane metadata only. It does not infer that a
container is stopped or that staging output is trustworthy after a restart.
Callers must reconcile runtime state before resuming a job.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Iterable


_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")
_CONTAINER_RE = re.compile(r"^yuki-job-[0-9a-f]{32}$")
_STATES = {
    "created", "running", "frozen", "validated", "promoted",
    "failed", "cancelled", "cleaned",
}


class SandboxJobStoreError(RuntimeError):
    """Durable sandbox job metadata could not be safely accessed or changed."""


@dataclass(frozen=True)
class DurableJobRecord:
    job_id: str
    state: str
    staging_directory: str
    container_name: str | None
    manifest_digest: str | None
    promoted_paths: tuple[str, ...]
    created_at: float
    updated_at: float


class SandboxJobStore:
    """Small transactional registry with compare-and-swap state transitions.

    The database parent must already exist, be owned by the current user, and
    have no group/other permission bits. The database itself is forced to 0600.
    """

    def __init__(self, database_path: str | Path):
        path = Path(database_path).expanduser()
        parent = path.parent
        if parent.is_symlink():
            raise ValueError("database parent cannot be a symlink")
        parent = parent.resolve(strict=True)
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("database parent must be a directory owned by the service user")
        if info.st_mode & 0o077:
            raise ValueError("database parent must not be accessible by group or others")
        path = parent / path.name
        if path.exists() or path.is_symlink():
            db_info = path.lstat()
            if stat.S_ISLNK(db_info.st_mode) or not stat.S_ISREG(db_info.st_mode):
                raise ValueError("database path must be a regular file, not a symlink")
            if db_info.st_uid != os.getuid():
                raise ValueError("database file must be owned by the service user")
            os.chmod(path, 0o600)
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=5.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sandbox_jobs (
                    job_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK(state IN (
                        'created','running','frozen','validated','promoted',
                        'failed','cancelled','cleaned'
                    )),
                    staging_directory TEXT NOT NULL,
                    container_name TEXT,
                    manifest_digest TEXT,
                    promoted_paths_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS sandbox_jobs_state_idx ON sandbox_jobs(state)"
            )
            os.chmod(self.path, 0o600)
        finally:
            connection.close()

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise SandboxJobStoreError("invalid sandbox job identifier")

    @staticmethod
    def _decode(row: sqlite3.Row) -> DurableJobRecord:
        try:
            paths = json.loads(row["promoted_paths_json"])
            if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
                raise ValueError("invalid promoted paths")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SandboxJobStoreError("durable job record is corrupt") from exc
        return DurableJobRecord(
            job_id=row["job_id"],
            state=row["state"],
            staging_directory=row["staging_directory"],
            container_name=row["container_name"],
            manifest_digest=row["manifest_digest"],
            promoted_paths=tuple(paths),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create(
        self, *, job_id: str, staging_directory: str, container_name: str | None = None
    ) -> DurableJobRecord:
        self._validate_job_id(job_id)
        if not isinstance(staging_directory, str) or not staging_directory.startswith("/"):
            raise SandboxJobStoreError("staging directory must be an absolute path")
        if container_name is not None and (
            not isinstance(container_name, str) or not _CONTAINER_RE.fullmatch(container_name)
        ):
            raise SandboxJobStoreError("invalid managed container name")
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO sandbox_jobs
                   (job_id,state,staging_directory,container_name,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (job_id, "created", staging_directory, container_name, now, now),
            )
            row = connection.execute(
                "SELECT * FROM sandbox_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            connection.execute("COMMIT")
            return self._decode(row)
        except sqlite3.IntegrityError as exc:
            connection.execute("ROLLBACK")
            raise SandboxJobStoreError("job already exists or violates registry constraints") from exc
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def transition(
        self,
        job_id: str,
        *,
        expected_states: Iterable[str],
        new_state: str,
        container_name: str | None = None,
        manifest_digest: str | None = None,
        promoted_paths: Iterable[str] | None = None,
    ) -> DurableJobRecord:
        self._validate_job_id(job_id)
        expected = tuple(expected_states)
        if not expected or any(state not in _STATES for state in expected) or new_state not in _STATES:
            raise SandboxJobStoreError("invalid state transition contract")
        if container_name is not None and (
            not isinstance(container_name, str) or not _CONTAINER_RE.fullmatch(container_name)
        ):
            raise SandboxJobStoreError("invalid managed container name")
        if manifest_digest is not None and (
            not isinstance(manifest_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest_digest)
        ):
            raise SandboxJobStoreError("invalid manifest digest")
        paths_json = None
        if promoted_paths is not None:
            paths = list(promoted_paths)
            if any(not isinstance(item, str) or not item or "\x00" in item for item in paths):
                raise SandboxJobStoreError("invalid promoted artifact path")
            paths_json = json.dumps(sorted(set(paths)), separators=(",", ":"))
        placeholders = ",".join("?" for _ in expected)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM sandbox_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if current is None or current["state"] not in expected:
                raise SandboxJobStoreError("job missing or state changed concurrently")
            connection.execute(
                f"""UPDATE sandbox_jobs SET state=?, updated_at=?,
                    container_name=COALESCE(?,container_name),
                    manifest_digest=COALESCE(?,manifest_digest),
                    promoted_paths_json=COALESCE(?,promoted_paths_json)
                    WHERE job_id=? AND state IN ({placeholders})""",
                (new_state, time.time(), container_name, manifest_digest,
                 paths_json, job_id, *expected),
            )
            if connection.total_changes < 1:
                raise SandboxJobStoreError("job state transition lost a concurrency race")
            row = connection.execute(
                "SELECT * FROM sandbox_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            connection.execute("COMMIT")
            return self._decode(row)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def get(self, job_id: str) -> DurableJobRecord | None:
        self._validate_job_id(job_id)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM sandbox_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            return self._decode(row) if row else None
        finally:
            connection.close()

    def list_nonterminal(self) -> tuple[DurableJobRecord, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT * FROM sandbox_jobs
                   WHERE state NOT IN ('promoted','failed','cancelled','cleaned')
                   ORDER BY created_at, job_id"""
            ).fetchall()
            return tuple(self._decode(row) for row in rows)
        finally:
            connection.close()
