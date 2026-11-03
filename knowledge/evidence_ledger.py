"""Evidence ledger for source-grounded knowledge claims.

A claim is not a fact merely because a model extracted it. Verification is an
explicit, attributable human/system review operation with independent sources.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4


class EvidenceLedger:
    MAX_CLAIM_CHARS = 2000
    MAX_NOTE_CHARS = 2000
    MAX_EVIDENCE_ITEMS = 50

    def __init__(self, knowledge_engine, database_path="YukiData/evidence.sqlite3"):
        if not callable(getattr(knowledge_engine, "get_chunks", None)):
            raise TypeError("knowledge_engine must provide get_chunks().")
        self.knowledge_engine = knowledge_engine
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(str(self.database_path), timeout=10.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def _session(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize(self):
        with self._lock, self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_claims (
                    claim_id TEXT PRIMARY KEY,
                    claim_text TEXT NOT NULL,
                    normalized_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('proposed','supported','contested','verified','rejected')),
                    created_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_claim_hash
                    ON knowledge_claims(normalized_hash);
                CREATE TABLE IF NOT EXISTS claim_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL REFERENCES knowledge_claims(claim_id)
                        ON DELETE CASCADE,
                    chunk_id INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    stance TEXT NOT NULL CHECK(stance IN ('supports','refutes','context')),
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE(claim_id, chunk_id, stance)
                );
                CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim
                    ON claim_evidence(claim_id);
                CREATE TABLE IF NOT EXISTS claim_reviews (
                    review_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL REFERENCES knowledge_claims(claim_id)
                        ON DELETE CASCADE,
                    decision TEXT NOT NULL CHECK(decision IN ('verify','reject','reopen')),
                    reviewer TEXT NOT NULL,
                    method TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
            """)

    def _resolve_chunks(self, chunk_ids):
        if not isinstance(chunk_ids, (list, tuple)) or not 1 <= len(chunk_ids) <= self.MAX_EVIDENCE_ITEMS:
            raise ValueError("Provide 1-50 canonical evidence chunk IDs.")
        if any(type(value) is not int or value <= 0 for value in chunk_ids):
            raise ValueError("Evidence chunk IDs must be positive integers.")
        unique = list(dict.fromkeys(chunk_ids))
        chunks = self.knowledge_engine.get_chunks(unique)
        by_id = {item.chunk_id: item for item in chunks}
        if set(by_id) != set(unique):
            raise ValueError("Evidence contains a missing or stale knowledge chunk.")
        return by_id

    @staticmethod
    def _text(value, name, maximum):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ValueError(f"{name} must be non-empty text of at most {maximum} characters.")
        return value.strip()

    def create_claim(self, claim_text, evidence, *, stance="supports", note=""):
        """Create a proposed claim linked to canonical chunks; never auto-verify it."""
        claim = self._text(claim_text, "claim_text", self.MAX_CLAIM_CHARS)
        note = "" if note == "" else self._text(note, "note", self.MAX_NOTE_CHARS)
        if stance not in {"supports", "refutes", "context"}:
            raise ValueError("stance must be supports, refutes, or context.")
        chunks = self._resolve_chunks(evidence)
        normalized_hash = hashlib.sha256(" ".join(claim.casefold().split()).encode()).hexdigest()
        claim_id = uuid4().hex
        with self._lock, self._session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO knowledge_claims
                       (claim_id, claim_text, normalized_hash, status)
                       VALUES (?, ?, ?, 'proposed')""",
                    (claim_id, claim, normalized_hash),
                )
                for chunk_id, chunk in chunks.items():
                    db.execute(
                        """INSERT INTO claim_evidence
                           (evidence_id, claim_id, chunk_id, source_id, stance, note)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (uuid4().hex, claim_id, chunk_id, chunk.source_id, stance, note),
                    )
                self._refresh_status(db, claim_id)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get_claim(claim_id)

    @staticmethod
    def _refresh_status(db, claim_id):
        row = db.execute(
            """SELECT
                 SUM(CASE WHEN stance='supports' THEN 1 ELSE 0 END) AS supporting,
                 SUM(CASE WHEN stance='refutes' THEN 1 ELSE 0 END) AS refuting
               FROM claim_evidence WHERE claim_id=?""", (claim_id,)
        ).fetchone()
        current = db.execute(
            "SELECT status FROM knowledge_claims WHERE claim_id=?", (claim_id,)
        ).fetchone()
        if current["status"] in {"verified", "rejected"}:
            return current["status"]
        supporting, refuting = row["supporting"] or 0, row["refuting"] or 0
        status = "contested" if supporting and refuting else "supported" if supporting else "proposed"
        db.execute(
            """UPDATE knowledge_claims SET status=?,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE claim_id=?""", (status, claim_id)
        )
        return status

    def add_evidence(self, claim_id, chunk_ids, *, stance, note=""):
        if stance not in {"supports", "refutes", "context"}:
            raise ValueError("stance must be supports, refutes, or context.")
        note = "" if note == "" else self._text(note, "note", self.MAX_NOTE_CHARS)
        chunks = self._resolve_chunks(chunk_ids)
        with self._lock, self._session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                claim = db.execute(
                    "SELECT status FROM knowledge_claims WHERE claim_id=?", (claim_id,)
                ).fetchone()
                if claim is None:
                    raise KeyError("Unknown claim.")
                if claim["status"] in {"verified", "rejected"}:
                    raise ValueError("Reviewed claims must be reopened before adding evidence.")
                added = 0
                for chunk_id, chunk in chunks.items():
                    cursor = db.execute(
                        """INSERT OR IGNORE INTO claim_evidence
                           (evidence_id, claim_id, chunk_id, source_id, stance, note)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (uuid4().hex, claim_id, chunk_id, chunk.source_id, stance, note),
                    )
                    added += cursor.rowcount
                self._refresh_status(db, claim_id)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"claim": self.get_claim(claim_id), "evidence_added": added}

    def review_claim(self, claim_id, *, decision, reviewer, method, rationale):
        """Record an explicit review; verification requires two distinct sources."""
        if decision not in {"verify", "reject", "reopen"}:
            raise ValueError("decision must be verify, reject, or reopen.")
        reviewer = self._text(reviewer, "reviewer", 200)
        method = self._text(method, "method", 300)
        rationale = self._text(rationale, "rationale", self.MAX_NOTE_CHARS)
        with self._lock, self._session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                claim = db.execute(
                    "SELECT status FROM knowledge_claims WHERE claim_id=?", (claim_id,)
                ).fetchone()
                if claim is None:
                    raise KeyError("Unknown claim.")
                evidence_rows = db.execute(
                    """SELECT chunk_id, source_id, stance FROM claim_evidence
                       WHERE claim_id=?""", (claim_id,)
                ).fetchall()
                sources = sorted({row["source_id"] for row in evidence_rows
                                  if row["stance"] in {"supports", "refutes"}})
                stances = {row["stance"] for row in evidence_rows}
                if decision == "verify":
                    evidence_chunk_ids = list(dict.fromkeys(
                        row["chunk_id"] for row in evidence_rows
                    ))
                    current = self.knowledge_engine.get_chunks(evidence_chunk_ids) if evidence_chunk_ids else []
                    current_by_id = {item.chunk_id: item.source_id for item in current}
                    if len(current_by_id) != len(evidence_chunk_ids) or any(
                        current_by_id.get(row["chunk_id"]) != row["source_id"]
                        for row in evidence_rows
                    ):
                        raise ValueError("Verification requires current canonical evidence; stale evidence must be replaced.")
                    if len(sources) < 2:
                        raise ValueError("Verification requires evidence from at least two distinct sources.")
                    if "refutes" in stances:
                        raise ValueError("Contested claims cannot be verified until conflicting evidence is resolved.")
                status = {"verify": "verified", "reject": "rejected", "reopen": "proposed"}[decision]
                db.execute(
                    """INSERT INTO claim_reviews
                       (review_id, claim_id, decision, reviewer, method, rationale, source_ids_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (uuid4().hex, claim_id, decision, reviewer, method, rationale,
                     json.dumps(sorted(sources))),
                )
                db.execute(
                    """UPDATE knowledge_claims SET status=?,
                       updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       WHERE claim_id=?""", (status, claim_id)
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get_claim(claim_id)

    def get_claim(self, claim_id):
        with self._lock, self._session() as db:
            claim = db.execute(
                "SELECT * FROM knowledge_claims WHERE claim_id=?", (claim_id,)
            ).fetchone()
            if claim is None:
                raise KeyError("Unknown claim.")
            evidence = db.execute(
                """SELECT evidence_id, chunk_id, source_id, stance, note, created_at
                   FROM claim_evidence WHERE claim_id=? ORDER BY created_at, evidence_id""",
                (claim_id,),
            ).fetchall()
            evidence_chunk_ids = list(dict.fromkeys(row["chunk_id"] for row in evidence))
            current_chunks = self.knowledge_engine.get_chunks(evidence_chunk_ids) if evidence_chunk_ids else []
            current_by_id = {item.chunk_id: item for item in current_chunks}
            reviews = db.execute(
                """SELECT review_id, decision, reviewer, method, rationale,
                          source_ids_json, created_at
                   FROM claim_reviews WHERE claim_id=? ORDER BY created_at, review_id""",
                (claim_id,),
            ).fetchall()
        return {
            "claim_id": claim["claim_id"], "claim_text": claim["claim_text"],
            "status": claim["status"], "created_at": claim["created_at"],
            "updated_at": claim["updated_at"],
            "evidence": [
                {
                    **dict(row),
                    "current": (
                        row["chunk_id"] in current_by_id
                        and current_by_id[row["chunk_id"]].source_id == row["source_id"]
                    ),
                    "content": (
                        current_by_id[row["chunk_id"]].content
                        if row["chunk_id"] in current_by_id
                        and current_by_id[row["chunk_id"]].source_id == row["source_id"]
                        else None
                    ),
                }
                for row in evidence
            ],
            "reviews": [
                {**dict(row), "source_ids": json.loads(row["source_ids_json"])}
                for row in reviews
            ],
        }

    def search_claims(self, query="", *, limit=50):
        if not isinstance(query, str):
            raise ValueError("query must be text.")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        with self._lock, self._session() as db:
            rows = db.execute(
                """SELECT claim_id FROM knowledge_claims
                   WHERE (? = '' OR claim_text LIKE ? ESCAPE '\\')
                   ORDER BY updated_at DESC LIMIT ?""",
                (query, "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", limit),
            ).fetchall()
        return [self.get_claim(row["claim_id"]) for row in rows]
