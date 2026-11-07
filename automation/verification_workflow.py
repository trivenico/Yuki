"""Bind task outputs to exact artifact digests and independent verification reports.

This durable workflow separates worker completion from verified acceptance.
It does not change coordinator task state; the caller must use the returned
decision to control downstream release/acceptance transitions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Callable

from automation.verification_evidence_store import VerificationEvidenceStore

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    plan_id: str
    subtask_id: str
    task_id: str
    author_agent_id: str
    artifact_sha256: str
    byte_length: int
    created_at: str


class VerificationWorkflowError(RuntimeError):
    pass


class VerificationWorkflow:
    """Append-only artifact-to-report linkage with reviewer authorization."""

    def __init__(
        self,
        database_path: str | Path,
        evidence_store: VerificationEvidenceStore,
        *,
        reviewer_authorize: Callable[[str, str, str], bool],
    ):
        if not isinstance(evidence_store, VerificationEvidenceStore):
            raise TypeError("evidence_store must be a VerificationEvidenceStore")
        if not callable(reviewer_authorize):
            raise TypeError("reviewer_authorize must be callable")
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path is required")
        self.evidence_store = evidence_store
        self.reviewer_authorize = reviewer_authorize
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS engineering_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    subtask_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    author_agent_id TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
                    created_at TEXT NOT NULL,
                    content BLOB NOT NULL,
                    UNIQUE(plan_id, subtask_id, artifact_id)
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(engineering_artifacts)")}
            if "content" not in columns:
                db.execute("ALTER TABLE engineering_artifacts ADD COLUMN content BLOB")
            db.execute("""
                CREATE TABLE IF NOT EXISTS artifact_verification_links (
                    artifact_id TEXT NOT NULL REFERENCES engineering_artifacts(artifact_id),
                    report_id TEXT NOT NULL,
                    linked_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(artifact_id, report_id)
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError(f"invalid_{name}")
        return value

    def record_artifact(
        self, *, plan_id: str, subtask_id: str, task_id: str,
        author_agent_id: str, content: bytes, created_at: str,
    ) -> ArtifactRecord:
        for value, name in ((plan_id, "plan_id"), (subtask_id, "subtask_id"),
                            (task_id, "task_id"), (author_agent_id, "author_agent_id")):
            self._identifier(value, name)
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        if not isinstance(created_at, str) or not created_at.strip() or len(created_at) > 64:
            raise ValueError("invalid_created_at")
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = hashlib.sha256(
            ("yuki-artifact-v1\0" + plan_id + "\0" + subtask_id + "\0" + task_id + "\0" + digest).encode()
        ).hexdigest()
        record = ArtifactRecord(artifact_id, plan_id, subtask_id, task_id,
                                author_agent_id, digest, len(content), created_at)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM engineering_artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if existing is not None:
                if any(existing[key] != value for key, value in asdict(record).items()):
                    raise VerificationWorkflowError("artifact_identity_conflict")
                if existing["content"] is not None and bytes(existing["content"]) != content:
                    raise VerificationWorkflowError("artifact_content_conflict")
                if existing["content"] is None:
                    db.execute("UPDATE engineering_artifacts SET content=? WHERE artifact_id=?", (content, artifact_id))
            else:
                db.execute(
                    """INSERT INTO engineering_artifacts
                       (artifact_id,plan_id,subtask_id,task_id,author_agent_id,artifact_sha256,byte_length,created_at,content)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (*tuple(asdict(record).values()), content),
                )
        return record

    def read_artifact(self, artifact_id: str) -> bytes:
        """Return exact immutable artifact bytes after rechecking their digest."""
        self._identifier(artifact_id, "artifact_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT content, artifact_sha256, byte_length FROM engineering_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError("artifact not found")
        if row["content"] is None:
            raise VerificationWorkflowError("artifact_content_unavailable")
        content = bytes(row["content"])
        if len(content) != row["byte_length"] or hashlib.sha256(content).hexdigest() != row["artifact_sha256"]:
            raise VerificationWorkflowError("artifact_content_integrity_mismatch")
        return content

    def link_report(
        self, *, artifact_id: str, report_id: str, reviewer_actor_id: str,
        created_at: str,
    ) -> dict:
        self._identifier(artifact_id, "artifact_id")
        self._identifier(reviewer_actor_id, "reviewer_actor_id")
        if not isinstance(report_id, str) or not _SHA.fullmatch(report_id):
            raise ValueError("invalid_report_id")
        if not isinstance(created_at, str) or not created_at.strip() or len(created_at) > 64:
            raise ValueError("invalid_created_at")
        if self.reviewer_authorize(reviewer_actor_id, "verification:submit", artifact_id) is not True:
            raise PermissionError("verification reviewer is not authorized")
        with self._connect() as db:
            artifact = db.execute(
                "SELECT * FROM engineering_artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
        if artifact is None:
            raise KeyError("artifact not found")
        stored = self.evidence_store.get(report_id)
        if stored is None:
            raise KeyError("verification report not found")
        report = stored["report"]
        decision = stored["decision"]
        if report["artifact_sha256"] != artifact["artifact_sha256"]:
            raise VerificationWorkflowError("verification_artifact_digest_mismatch")
        if report["author_agent_id"] != artifact["author_agent_id"]:
            raise VerificationWorkflowError("verification_author_mismatch")
        reviewer_ids = {check["reviewer_agent_id"] for check in report["checks"]}
        if artifact["author_agent_id"] in reviewer_ids:
            raise VerificationWorkflowError("artifact_author_self_review")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT OR IGNORE INTO artifact_verification_links
                   (artifact_id, report_id, linked_by, created_at) VALUES (?, ?, ?, ?)""",
                (artifact_id, report_id, reviewer_actor_id, created_at),
            )
        return {
            "artifact_id": artifact_id,
            "report_id": report_id,
            "accepted": bool(decision["accepted"]),
            "decision": decision,
        }

    def get_status(self, artifact_id: str) -> dict:
        self._identifier(artifact_id, "artifact_id")
        with self._connect() as db:
            artifact = db.execute(
                "SELECT * FROM engineering_artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if artifact is None:
                raise KeyError("artifact not found")
            links = db.execute(
                """SELECT report_id, linked_by, created_at FROM artifact_verification_links
                   WHERE artifact_id=? ORDER BY created_at, report_id""", (artifact_id,)
            ).fetchall()
        reports = []
        for link in links:
            stored = self.evidence_store.get(link["report_id"])
            if stored is not None:
                reports.append({
                    "report_id": link["report_id"],
                    "accepted": bool(stored["decision"]["accepted"]),
                    "linked_by": link["linked_by"],
                    "created_at": link["created_at"],
                })
        return {
            "artifact": dict(artifact),
            "verified": any(item["accepted"] for item in reports),
            "reports": reports,
        }
