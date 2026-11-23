"""Append-only owner-scoped evidence accepted only after host attestation.

The injected verifier is the trust boundary: callers must wire it to an
authenticated CI/review/artifact verifier. This module never treats a task's
self-reported success or a caller-provided boolean as proof.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import sqlite3
from typing import Callable
from uuid import uuid4

from core.sqlite_file_security import prepare_private_sqlite_path

_EVIDENCE_KINDS = frozenset({"ci_workflow", "test_report", "artifact", "human_review"})
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_TEXT = 500


def _required(value: str, field: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be non-empty bounded text.")
    return value.strip()


def _timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("verified_at must be a timezone-aware ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("verified_at must be a timezone-aware ISO timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("verified_at must be a timezone-aware ISO timestamp.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class EvidenceAttestation:
    owner_id: str
    task_id: str
    task_version: int
    kind: str
    reference: str
    digest_sha256: str
    verifier_id: str
    verified_at: str


@dataclass(frozen=True, slots=True)
class TaskEvidenceRecord:
    evidence_id: str
    owner_id: str
    task_id: str
    task_version: int
    kind: str
    reference: str
    digest_sha256: str
    verifier_id: str
    verified_at: str
    recorded_at: str


class TaskEvidenceStore:
    """Durable evidence ledger; only a trusted, injected verifier can admit records."""

    def __init__(
        self, database_path: str, *,
        attestation_verifier: Callable[[EvidenceAttestation], bool],
    ):
        if not callable(attestation_verifier):
            raise ValueError("a trusted attestation_verifier is required.")
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        self._verifier = attestation_verifier
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_version INTEGER NOT NULL CHECK(task_version > 0),
                    kind TEXT NOT NULL CHECK(kind IN ('ci_workflow','test_report','artifact','human_review')),
                    reference TEXT NOT NULL,
                    digest_sha256 TEXT NOT NULL CHECK(length(digest_sha256)=64),
                    verifier_id TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(owner_id, task_id, task_version, digest_sha256, verifier_id)
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(task_evidence)").fetchall()}
            if "task_version" not in columns:
                # The earlier ledger schema did not bind proof to an attempt.
                # Preserve those already host-verified records as version 1.
                db.execute("DROP TRIGGER IF EXISTS task_evidence_no_update")
                db.execute("DROP TRIGGER IF EXISTS task_evidence_no_delete")
                db.execute("ALTER TABLE task_evidence RENAME TO task_evidence_legacy")
                db.execute("""
                    CREATE TABLE task_evidence (
                        evidence_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        task_version INTEGER NOT NULL CHECK(task_version > 0),
                        kind TEXT NOT NULL CHECK(kind IN ('ci_workflow','test_report','artifact','human_review')),
                        reference TEXT NOT NULL,
                        digest_sha256 TEXT NOT NULL CHECK(length(digest_sha256)=64),
                        verifier_id TEXT NOT NULL,
                        verified_at TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        UNIQUE(owner_id, task_id, task_version, digest_sha256, verifier_id)
                    )
                """)
                db.execute("""
                    INSERT INTO task_evidence
                    (evidence_id, owner_id, task_id, task_version, kind, reference,
                     digest_sha256, verifier_id, verified_at, recorded_at)
                    SELECT evidence_id, owner_id, task_id, 1, kind, reference,
                           digest_sha256, verifier_id, verified_at, recorded_at
                    FROM task_evidence_legacy
                """)
                db.execute("DROP TABLE task_evidence_legacy")
            db.execute("CREATE INDEX IF NOT EXISTS idx_task_evidence_owner_task ON task_evidence(owner_id, task_id, recorded_at)")
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS task_evidence_no_update
                BEFORE UPDATE ON task_evidence BEGIN
                    SELECT RAISE(ABORT, 'task evidence is append-only');
                END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS task_evidence_no_delete
                BEFORE DELETE ON task_evidence BEGIN
                    SELECT RAISE(ABORT, 'task evidence is append-only');
                END
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _validate(attestation: EvidenceAttestation) -> EvidenceAttestation:
        if not isinstance(attestation, EvidenceAttestation):
            raise TypeError("attestation must be an EvidenceAttestation.")
        owner = _required(attestation.owner_id, "owner_id")
        task = _required(attestation.task_id, "task_id")
        if type(attestation.task_version) is not int or attestation.task_version < 1:
            raise ValueError("task_version must be a positive integer.")
        kind = _required(attestation.kind, "kind", 40)
        if kind not in _EVIDENCE_KINDS:
            raise ValueError("unsupported evidence kind.")
        reference = _required(attestation.reference, "reference", _MAX_TEXT)
        if any(ord(ch) < 32 for ch in reference):
            raise ValueError("reference contains control characters.")
        digest = _required(attestation.digest_sha256, "digest_sha256", 64).lower()
        if not _SHA256.fullmatch(digest):
            raise ValueError("digest_sha256 must be a 64-character SHA-256 hex digest.")
        verifier = _required(attestation.verifier_id, "verifier_id", 120)
        verified_at = _timestamp(attestation.verified_at)
        return EvidenceAttestation(owner, task, attestation.task_version, kind, reference, digest, verifier, verified_at)

    def record_verified(self, attestation: EvidenceAttestation) -> TaskEvidenceRecord:
        """Admit evidence only when the host's verifier validates the complete attestation."""
        item = self._validate(attestation)
        try:
            accepted = self._verifier(item)
        except Exception as exc:
            raise PermissionError("evidence attestation could not be verified.") from exc
        if accepted is not True:
            raise PermissionError("evidence attestation was not verified.")
        evidence_id = "evd_" + uuid4().hex
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO task_evidence "
                "(evidence_id, owner_id, task_id, task_version, kind, reference, digest_sha256, verifier_id, verified_at, recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, item.owner_id, item.task_id, item.task_version, item.kind, item.reference,
                 item.digest_sha256, item.verifier_id, item.verified_at, recorded_at),
            )
            row = db.execute(
                "SELECT * FROM task_evidence WHERE owner_id=? AND task_id=? AND task_version=? AND digest_sha256=? AND verifier_id=?",
                (item.owner_id, item.task_id, item.task_version, item.digest_sha256, item.verifier_id),
            ).fetchone()
        return self._record(row)

    def list_verified(self, *, owner_id: str, task_id: str, task_version: int | None = None, limit: int = 50) -> tuple[TaskEvidenceRecord, ...]:
        owner = _required(owner_id, "owner_id")
        task = _required(task_id, "task_id")
        if task_version is not None and (type(task_version) is not int or task_version < 1):
            raise ValueError("task_version must be a positive integer when supplied.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            if task_version is None:
                rows = db.execute(
                    "SELECT * FROM task_evidence WHERE owner_id=? AND task_id=? ORDER BY recorded_at DESC LIMIT ?",
                    (owner, task, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM task_evidence WHERE owner_id=? AND task_id=? AND task_version=? ORDER BY recorded_at DESC LIMIT ?",
                    (owner, task, task_version, limit),
                ).fetchall()
        return tuple(self._record(row) for row in rows)

    @staticmethod
    def _record(row: sqlite3.Row) -> TaskEvidenceRecord:
        return TaskEvidenceRecord(
            row["evidence_id"], row["owner_id"], row["task_id"], row["task_version"], row["kind"],
            row["reference"], row["digest_sha256"], row["verifier_id"],
            row["verified_at"], row["recorded_at"],
        )
