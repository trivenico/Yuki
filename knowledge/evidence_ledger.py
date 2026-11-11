"""Owner-scoped, provenance-aware claim ledger.

Claims are proposals until an explicit trusted caller reviews them. Evidence stores
canonical source/chunk identifiers and offsets, never a copied source document.
Reviewer authentication and source-index lifecycle remain caller responsibilities.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Callable
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceLedger:
    """SQLite ledger for claims, source evidence, and append-only review events."""

    MAX_CLAIM_TEXT = 4000
    MAX_SOURCE_ID = 256
    MAX_CHUNK_ID = 256
    MAX_EVIDENCE_PER_CLAIM = 100

    def __init__(self, database_path: str | Path, *, owner_id: str) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be non-empty text of at most 200 characters.")
        raw_path = Path(database_path).expanduser()
        if raw_path.exists() and raw_path.is_symlink():
            raise ValueError("evidence ledger database must not be a symlink.")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.parent.is_symlink():
            raise ValueError("evidence ledger directory must not be a symlink.")
        self.database_path = raw_path
        self.owner_id = owner_id.strip()
        self._initialize()
        if os.name == "posix":
            try:
                os.chmod(self.database_path, 0o600)
            except OSError as exc:
                raise RuntimeError("Could not secure evidence ledger permissions.") from exc

    def _connect(self) -> sqlite3.Connection:
        if self.database_path.is_symlink():
            raise RuntimeError("evidence ledger database must not be a symlink.")
        db = sqlite3.connect(str(self.database_path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        db = self._connect()
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_claims (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('proposed','verified','rejected')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_by TEXT,
                    reviewed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_claims_owner_updated
                    ON knowledge_claims(owner_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS knowledge_evidence (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL REFERENCES knowledge_claims(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    stance TEXT NOT NULL CHECK(stance IN ('supports','refutes')),
                    span_start INTEGER,
                    span_end INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(owner_id, claim_id, source_id, chunk_id, stance)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_claim
                    ON knowledge_evidence(owner_id, claim_id);
                CREATE TABLE IF NOT EXISTS knowledge_review_events (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL REFERENCES knowledge_claims(id) ON DELETE CASCADE,
                    reviewer_id TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('verified','rejected','reopened')),
                    rationale TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_claim
                    ON knowledge_review_events(owner_id, claim_id, created_at);
            """)
        finally:
            db.close()

    @staticmethod
    def _validate_identifier(value: str, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must be non-empty text of at most {maximum} characters.")
        return value.strip()

    def create_claim(self, text: str) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_CLAIM_TEXT:
            raise ValueError(f"claim text must be 1-{self.MAX_CLAIM_TEXT} characters.")
        claim_id, now = uuid4().hex, _now()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO knowledge_claims(id,owner_id,text,status,created_at,updated_at) VALUES(?,?,?,'proposed',?,?)",
                (claim_id, self.owner_id, text.strip(), now, now),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.get_claim(claim_id)

    def add_evidence(
        self, claim_id: str, *, source_id: str, chunk_id: str,
        stance: str = "supports", span_start: int | None = None,
        span_end: int | None = None,
    ) -> dict:
        claim_id = self._validate_identifier(claim_id, "claim_id", 64)
        source_id = self._validate_identifier(source_id, "source_id", self.MAX_SOURCE_ID)
        chunk_id = self._validate_identifier(chunk_id, "chunk_id", self.MAX_CHUNK_ID)
        if stance not in {"supports", "refutes"}:
            raise ValueError("stance must be supports or refutes.")
        if (span_start is None) != (span_end is None):
            raise ValueError("evidence span requires both start and end offsets.")
        if span_start is not None and (
            isinstance(span_start, bool) or isinstance(span_end, bool)
            or not isinstance(span_start, int) or not isinstance(span_end, int)
            or span_start < 0 or span_end <= span_start
        ):
            raise ValueError("evidence span offsets must be non-negative and increasing.")
        evidence_id, now = uuid4().hex, _now()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            claim = db.execute(
                "SELECT status FROM knowledge_claims WHERE id=? AND owner_id=?",
                (claim_id, self.owner_id),
            ).fetchone()
            if claim is None:
                raise KeyError("claim not found")
            if claim["status"] != "proposed":
                raise ValueError("reopen the claim before changing its evidence.")
            count = db.execute(
                "SELECT COUNT(*) AS n FROM knowledge_evidence WHERE owner_id=? AND claim_id=?",
                (self.owner_id, claim_id),
            ).fetchone()["n"]
            if count >= self.MAX_EVIDENCE_PER_CLAIM:
                raise ValueError("claim evidence limit reached.")
            db.execute(
                """INSERT INTO knowledge_evidence
                   (id,owner_id,claim_id,source_id,chunk_id,stance,span_start,span_end,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (evidence_id, self.owner_id, claim_id, source_id, chunk_id,
                 stance, span_start, span_end, now),
            )
            db.execute(
                "UPDATE knowledge_claims SET updated_at=? WHERE id=? AND owner_id=?",
                (now, claim_id, self.owner_id),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.get_claim(claim_id)

    def review_claim(
        self, claim_id: str, *, reviewer_id: str, decision: str,
        rationale: str = "", source_is_current: Callable[[str, str], bool] | None = None,
    ) -> dict:
        claim_id = self._validate_identifier(claim_id, "claim_id", 64)
        reviewer_id = self._validate_identifier(reviewer_id, "reviewer_id", 200)
        if decision not in {"verified", "rejected"}:
            raise ValueError("decision must be verified or rejected.")
        if not isinstance(rationale, str) or len(rationale) > 2000:
            raise ValueError("rationale must be at most 2000 characters.")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            claim = db.execute(
                "SELECT status FROM knowledge_claims WHERE id=? AND owner_id=?",
                (claim_id, self.owner_id),
            ).fetchone()
            if claim is None:
                raise KeyError("claim not found")
            if claim["status"] != "proposed":
                raise ValueError("claim must be proposed before review.")
            evidence = db.execute(
                "SELECT source_id,chunk_id,stance FROM knowledge_evidence WHERE owner_id=? AND claim_id=?",
                (self.owner_id, claim_id),
            ).fetchall()
            if decision == "verified":
                supporting_sources = {row["source_id"] for row in evidence if row["stance"] == "supports"}
                if len(supporting_sources) < 2:
                    raise ValueError("verification requires supporting evidence from two distinct sources.")
                if any(row["stance"] == "refutes" for row in evidence):
                    raise ValueError("recorded refuting evidence must be resolved before verification.")
                if not callable(source_is_current):
                    raise ValueError("verification requires a current-source resolver.")
                if any(not source_is_current(row["source_id"], row["chunk_id"]) for row in evidence):
                    raise ValueError("all supporting evidence must resolve to current source chunks.")
            now = _now()
            db.execute(
                """UPDATE knowledge_claims SET status=?,updated_at=?,reviewed_by=?,reviewed_at=?
                   WHERE id=? AND owner_id=?""",
                (decision, now, reviewer_id, now, claim_id, self.owner_id),
            )
            db.execute(
                """INSERT INTO knowledge_review_events
                   (id,owner_id,claim_id,reviewer_id,decision,rationale,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (uuid4().hex, self.owner_id, claim_id, reviewer_id, decision,
                 rationale.strip(), now),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.get_claim(claim_id, source_is_current=source_is_current)

    def reopen_claim(self, claim_id: str, *, reviewer_id: str, rationale: str = "") -> dict:
        claim_id = self._validate_identifier(claim_id, "claim_id", 64)
        reviewer_id = self._validate_identifier(reviewer_id, "reviewer_id", 200)
        if not isinstance(rationale, str) or len(rationale) > 2000:
            raise ValueError("rationale must be at most 2000 characters.")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            claim = db.execute(
                "SELECT status FROM knowledge_claims WHERE id=? AND owner_id=?",
                (claim_id, self.owner_id),
            ).fetchone()
            if claim is None:
                raise KeyError("claim not found")
            if claim["status"] == "proposed":
                raise ValueError("claim is already proposed.")
            now = _now()
            db.execute(
                """UPDATE knowledge_claims SET status='proposed',updated_at=?,reviewed_by=NULL,reviewed_at=NULL
                   WHERE id=? AND owner_id=?""",
                (now, claim_id, self.owner_id),
            )
            db.execute(
                """INSERT INTO knowledge_review_events
                   (id,owner_id,claim_id,reviewer_id,decision,rationale,created_at)
                   VALUES(?,?,?,?,'reopened',?,?)""",
                (uuid4().hex, self.owner_id, claim_id, reviewer_id, rationale.strip(), now),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.get_claim(claim_id)

    def get_claim(
        self, claim_id: str, *,
        source_is_current: Callable[[str, str], bool] | None = None,
    ) -> dict:
        claim_id = self._validate_identifier(claim_id, "claim_id", 64)
        db = self._connect()
        try:
            row = db.execute(
                """SELECT id,text,status,created_at,updated_at,reviewed_by,reviewed_at
                   FROM knowledge_claims WHERE id=? AND owner_id=?""",
                (claim_id, self.owner_id),
            ).fetchone()
            if row is None:
                raise KeyError("claim not found")
            evidence = db.execute(
                """SELECT id,source_id,chunk_id,stance,span_start,span_end,created_at
                   FROM knowledge_evidence WHERE owner_id=? AND claim_id=? ORDER BY created_at,id""",
                (self.owner_id, claim_id),
            ).fetchall()
            reviews = db.execute(
                """SELECT reviewer_id,decision,rationale,created_at
                   FROM knowledge_review_events WHERE owner_id=? AND claim_id=? ORDER BY created_at,id""",
                (self.owner_id, claim_id),
            ).fetchall()
            result = dict(row)
            result["evidence"] = []
            for item in evidence:
                entry = dict(item)
                if source_is_current is None:
                    entry["freshness"] = "unknown"
                else:
                    try:
                        entry["freshness"] = "current" if source_is_current(entry["source_id"], entry["chunk_id"]) else "stale"
                    except Exception:
                        entry["freshness"] = "unknown"
                result["evidence"].append(entry)
            result["reviews"] = [dict(item) for item in reviews]
            return result
        finally:
            db.close()

    def list_claims(self, *, status: str | None = None, limit: int = 100) -> list[dict]:
        if status is not None and status not in {"proposed", "verified", "rejected"}:
            raise ValueError("invalid claim status.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        db = self._connect()
        try:
            if status is None:
                rows = db.execute(
                    """SELECT id,text,status,created_at,updated_at,reviewed_by,reviewed_at
                       FROM knowledge_claims WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?""",
                    (self.owner_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT id,text,status,created_at,updated_at,reviewed_by,reviewed_at
                       FROM knowledge_claims WHERE owner_id=? AND status=? ORDER BY updated_at DESC LIMIT ?""",
                    (self.owner_id, status, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()

    def forget_claim(self, claim_id: str) -> bool:
        claim_id = self._validate_identifier(claim_id, "claim_id", 64)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "DELETE FROM knowledge_claims WHERE id=? AND owner_id=?",
                (claim_id, self.owner_id),
            )
            db.commit()
            return result.rowcount == 1
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
