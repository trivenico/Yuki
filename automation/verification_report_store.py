"""Append-only SQLite persistence for independent verification reports."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from automation.independent_verification import VerificationReport


class VerificationReportStoreError(RuntimeError):
    pass


class VerificationReportStore:
    """Persist immutable, digest-bound reports; no update/delete API is exposed."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS verification_reports (
                    report_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    report_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workflow_id, artifact_sha256)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_verification_reports_workflow "
                "ON verification_reports(workflow_id, created_at)"
            )

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _serialize(report: VerificationReport) -> str:
        if not isinstance(report, VerificationReport):
            raise TypeError("report must be a VerificationReport.")
        payload = asdict(report)
        payload["findings"] = [asdict(item) for item in report.findings]
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("verification report exceeds 64 KiB.")
        return encoded

    def save(self, *, workflow_id: str, report: VerificationReport) -> dict[str, Any]:
        if not isinstance(workflow_id, str) or not workflow_id.strip() or len(workflow_id) > 200:
            raise ValueError("workflow_id must be bounded non-empty text.")
        encoded = self._serialize(report)
        report_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        report_id = "vr_" + report_digest[:32]
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT * FROM verification_reports WHERE workflow_id=? AND artifact_sha256=?",
                    (workflow_id.strip(), report.artifact_sha256),
                ).fetchone()
                if existing:
                    if existing["report_sha256"] != report_digest:
                        raise VerificationReportStoreError(
                            "a different report already exists for this workflow artifact."
                        )
                    return self._row(existing)
                db.execute("""
                    INSERT INTO verification_reports
                    (report_id, workflow_id, artifact_sha256, report_json, report_sha256, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (report_id, workflow_id.strip(), report.artifact_sha256,
                      encoded, report_digest, created_at))
                row = db.execute(
                    "SELECT * FROM verification_reports WHERE report_id=?", (report_id,)
                ).fetchone()
                return self._row(row)
        except sqlite3.IntegrityError as exc:
            raise VerificationReportStoreError("verification report persistence conflict.") from exc

    @staticmethod
    def _row(row):
        payload = json.loads(row["report_json"])
        digest = hashlib.sha256(row["report_json"].encode("utf-8")).hexdigest()
        if digest != row["report_sha256"]:
            raise VerificationReportStoreError("stored verification report integrity check failed.")
        return {
            "report_id": row["report_id"],
            "workflow_id": row["workflow_id"],
            "artifact_sha256": row["artifact_sha256"],
            "report_sha256": row["report_sha256"],
            "created_at": row["created_at"],
            "report": payload,
        }

    def get(self, report_id: str) -> dict[str, Any] | None:
        if not isinstance(report_id, str) or not report_id.startswith("vr_"):
            raise ValueError("report_id is invalid.")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM verification_reports WHERE report_id=?", (report_id,)
            ).fetchone()
            return self._row(row) if row else None

    def list_for_workflow(self, workflow_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("workflow_id is required.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM verification_reports WHERE workflow_id=? "
                "ORDER BY created_at DESC LIMIT ?", (workflow_id.strip(), limit)
            ).fetchall()
            return [self._row(row) for row in rows]
