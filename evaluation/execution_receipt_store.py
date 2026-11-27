"""Owner-scoped immutable persistence for candidate execution evidence.

The SQLite journal is tamper-evident at the application layer, not an external
witness. A privileged database owner can replace rows and triggers; signatures
from a separately protected runner identity remain a production requirement.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from typing import Any

from core.sqlite_file_security import prepare_private_sqlite_path
from evaluation.execution_receipts import (
    CandidateExecutionManifest,
    CandidateExecutionReceipt,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in stored execution evidence")
        result[key] = value
    return result


def _manifest_from_json(raw: str) -> CandidateExecutionManifest:
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != set(CandidateExecutionManifest.__dataclass_fields__):
        raise ValueError("stored execution manifest schema is invalid")
    result = CandidateExecutionManifest(**value)
    if not result.verify() or _canonical(asdict(result)) != raw:
        raise ValueError("stored execution manifest failed integrity verification")
    return result


def _receipt_from_json(raw: str, manifest: CandidateExecutionManifest) -> CandidateExecutionReceipt:
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != set(CandidateExecutionReceipt.__dataclass_fields__):
        raise ValueError("stored execution receipt schema is invalid")
    result = CandidateExecutionReceipt(**value)
    if not result.verify(manifest) or _canonical(asdict(result)) != raw:
        raise ValueError("stored execution receipt failed integrity verification")
    return result


class CandidateExecutionReceiptStore:
    """Append-only-by-contract SQLite journal for verified manifest/receipt pairs."""

    def __init__(self, database_path: str | Path, *, max_records: int = 100_000):
        if type(max_records) is not int or not 1 <= max_records <= 1_000_000:
            raise ValueError("max_records must be an integer between 1 and 1000000")
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.max_records = max_records
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS candidate_execution_receipts (
                    owner_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    receipt_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    PRIMARY KEY(owner_id, attempt_id),
                    UNIQUE(owner_id, receipt_digest)
                )
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS candidate_execution_receipts_no_update
                BEFORE UPDATE ON candidate_execution_receipts
                BEGIN SELECT RAISE(ABORT, 'execution receipt records are immutable'); END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS candidate_execution_receipts_no_delete
                BEFORE DELETE ON candidate_execution_receipts
                BEGIN SELECT RAISE(ABORT, 'execution receipt records are immutable'); END
            """)

    def _connect(self):
        self.database_path = prepare_private_sqlite_path(self.database_path)
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return _ClosingConnection(db)

    def record(
        self, *, owner_id: str, manifest: CandidateExecutionManifest,
        receipt: CandidateExecutionReceipt,
    ) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200 or "\x00" in owner_id:
            raise ValueError("owner_id must be bounded non-empty text")
        if not isinstance(manifest, CandidateExecutionManifest) or not manifest.verify():
            raise ValueError("manifest failed integrity verification")
        if not isinstance(receipt, CandidateExecutionReceipt) or not receipt.verify(manifest):
            raise ValueError("receipt failed manifest-bound integrity verification")
        if owner_id.strip() != manifest.owner_id:
            raise ValueError("owner does not match execution manifest")
        manifest_json = _canonical(asdict(manifest))
        receipt_json = _canonical(asdict(receipt))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) AS n FROM candidate_execution_receipts WHERE owner_id=?",
                (owner_id.strip(),),
            ).fetchone()["n"]
            if count >= self.max_records:
                raise ValueError("execution receipt journal capacity reached")
            try:
                db.execute(
                    "INSERT INTO candidate_execution_receipts "
                    "(owner_id,attempt_id,candidate_id,candidate_digest,manifest_digest,"
                    "receipt_digest,manifest_json,receipt_json) VALUES(?,?,?,?,?,?,?,?)",
                    (owner_id.strip(), manifest.attempt_id, manifest.candidate_id,
                     manifest.candidate_digest, manifest.manifest_digest,
                     receipt.receipt_digest, manifest_json, receipt_json),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("execution attempt is already recorded") from exc

    def get(self, *, owner_id: str, attempt_id: str) -> tuple[CandidateExecutionManifest, CandidateExecutionReceipt]:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be bounded non-empty text")
        if not isinstance(attempt_id, str) or not attempt_id.strip() or len(attempt_id) > 128:
            raise ValueError("attempt_id must be bounded non-empty text")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM candidate_execution_receipts WHERE owner_id=? AND attempt_id=?",
                (owner_id.strip(), attempt_id.strip()),
            ).fetchone()
        if row is None:
            raise KeyError("candidate execution receipt not found")
        manifest = _manifest_from_json(row["manifest_json"])
        receipt = _receipt_from_json(row["receipt_json"], manifest)
        if (manifest.owner_id != owner_id.strip()
                or manifest.attempt_id != attempt_id.strip()
                or manifest.candidate_id != row["candidate_id"]
                or manifest.candidate_digest != row["candidate_digest"]
                or manifest.manifest_digest != row["manifest_digest"]
                or receipt.receipt_digest != row["receipt_digest"]):
            raise RuntimeError("execution receipt journal index failed integrity verification")
        return manifest, receipt

    def list_for_candidate(
        self, *, owner_id: str, candidate_id: str, limit: int = 100,
    ) -> tuple[tuple[CandidateExecutionManifest, CandidateExecutionReceipt], ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self._connect() as db:
            rows = db.execute(
                "SELECT attempt_id FROM candidate_execution_receipts "
                "WHERE owner_id=? AND candidate_id=? ORDER BY attempt_id LIMIT ?",
                (owner_id, candidate_id, limit),
            ).fetchall()
        return tuple(self.get(owner_id=owner_id, attempt_id=row["attempt_id"]) for row in rows)


class _ClosingConnection:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is not None:
                if self.connection.in_transaction:
                    self.connection.rollback()
            elif self.connection.in_transaction:
                self.connection.commit()
        finally:
            self.connection.close()
        return False
