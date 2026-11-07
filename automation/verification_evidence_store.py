"""Durable, immutable storage for independent verification reports.

The store records submitted evidence and its deterministic decision. It does not
execute checks or authenticate evidence references; callers must obtain reports
from trusted, authorized verification workers.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable, Iterable

from automation.independent_verification import (
    VerificationCheck, VerificationReport, evaluate_verification,
)


class VerificationStoreError(RuntimeError):
    pass


class VerificationConflict(VerificationStoreError):
    pass


class VerificationEvidenceStore:
    """SQLite-backed append-only report store with idempotent report creation."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        execution_evidence_validator: Callable[[str, str, str], bool] | None = None,
        reviewer_authorizer: Callable[[str, str, str], bool] | None = None,
    ):
        self.database_path = str(database_path)
        if execution_evidence_validator is not None and not callable(execution_evidence_validator):
            raise TypeError("execution_evidence_validator must be callable")
        self.execution_evidence_validator = execution_evidence_validator
        if reviewer_authorizer is not None and not callable(reviewer_authorizer):
            raise TypeError("reviewer_authorizer must be callable")
        self.reviewer_authorizer = reviewer_authorizer
        if not self.database_path.strip():
            raise ValueError("database_path is required")
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self):
        with closing(self._connect()) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS verification_reports (
                    report_id TEXT PRIMARY KEY,
                    artifact_sha256 TEXT NOT NULL,
                    author_agent_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(artifact_sha256, report_id)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_verification_artifact
                ON verification_reports(artifact_sha256, created_at)
            """)

    @staticmethod
    def _serialize(report: VerificationReport) -> str:
        value = {
            "artifact_sha256": report.artifact_sha256,
            "author_agent_id": report.author_agent_id,
            "created_at": report.created_at,
            "checks": [asdict(check) for check in report.checks],
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _report_id(serialized: str) -> str:
        return hashlib.sha256(("yuki-verification-report-v1\0" + serialized).encode()).hexdigest()

    def record(self, report: VerificationReport, *, required_checks: Iterable[str] | None = None) -> dict:
        """Persist a validated report; exact retries return the original record."""
        if self.reviewer_authorizer is None:
            raise VerificationStoreError("trusted_reviewer_authorizer_not_configured")
        if self.reviewer_authorizer(
            report.author_agent_id, "verification:submit", report.artifact_sha256,
        ) is not True:
            raise PermissionError("verification report author is not authorized")
        for check in report.checks:
            if self.reviewer_authorizer(
                check.reviewer_agent_id, "verification:review", report.artifact_sha256,
            ) is not True:
                raise PermissionError("verification reviewer is not authorized")
        decision = evaluate_verification(
            report,
            **({"required_checks": required_checks} if required_checks is not None else {}),
        )
        for check in report.checks:
            requires_execution_receipt = (
                self.execution_evidence_validator is not None
                and check.name in {"tests", "security"}
            )
            is_execution_reference = check.evidence_reference.startswith(
                "yuki-verification-execution://"
            )
            if requires_execution_receipt and not is_execution_reference:
                raise VerificationStoreError("execution_receipt_required_for_check")
            if is_execution_reference:
                if self.execution_evidence_validator is None:
                    raise VerificationStoreError("execution_receipt_validator_not_configured")
                if self.execution_evidence_validator(
                    check.evidence_reference, report.artifact_sha256, check.name,
                ) is not True:
                    raise VerificationStoreError("execution_receipt_invalid_or_not_successful")
        serialized = self._serialize(report)
        report_id = self._report_id(serialized)
        decision_json = json.dumps(asdict(decision), sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT report_json, decision_json FROM verification_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if existing is not None:
                if existing["report_json"] != serialized or existing["decision_json"] != decision_json:
                    raise VerificationConflict("verification_report_identity_conflict")
                return {"report_id": report_id, "decision": asdict(decision), "reused": True}
            db.execute(
                """INSERT INTO verification_reports(
                    report_id, artifact_sha256, author_agent_id,
                    report_json, decision_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (report_id, report.artifact_sha256, report.author_agent_id,
                 serialized, decision_json, report.created_at),
            )
        return {"report_id": report_id, "decision": asdict(decision), "reused": False}

    def get(self, report_id: str) -> dict | None:
        if not isinstance(report_id, str) or len(report_id) != 64 or any(c not in "0123456789abcdef" for c in report_id):
            raise ValueError("report_id must be a lowercase SHA-256 digest")
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT report_json, decision_json FROM verification_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "report": json.loads(row["report_json"]),
            "decision": json.loads(row["decision_json"]),
        }

    def list_for_artifact(self, artifact_sha256: str, *, limit: int = 20) -> tuple[dict, ...]:
        if not isinstance(artifact_sha256, str) or len(artifact_sha256) != 64 or any(c not in "0123456789abcdef" for c in artifact_sha256):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256 digest")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with closing(self._connect()) as db, db:
            rows = db.execute(
                """SELECT report_id, decision_json, created_at
                   FROM verification_reports WHERE artifact_sha256=?
                   ORDER BY created_at DESC, report_id LIMIT ?""",
                (artifact_sha256, limit),
            ).fetchall()
        return tuple({
            "report_id": row["report_id"],
            "decision": json.loads(row["decision_json"]),
            "created_at": row["created_at"],
        } for row in rows)
