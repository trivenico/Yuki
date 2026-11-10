"""Evidence-backed independent specialist review and deterministic consensus."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from typing import Callable, Iterable, Mapping

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_VERDICTS = frozenset({"approve", "revise", "reject"})
_MAX_FINDINGS = 30
_MAX_REVIEW_BYTES = 16_000


class EvaluationError(ValueError):
    """Invalid review or evaluation request."""


class EvaluationConflict(EvaluationError):
    """An idempotency key was reused with a different review."""


@dataclass(frozen=True)
class AgentReview:
    review_id: int
    subject_id: str
    reviewer_id: str
    verdict: str
    scores: Mapping[str, int]
    findings: tuple[str, ...]
    idempotency_key: str
    created_at: str


@dataclass(frozen=True)
class ConsensusResult:
    subject_id: str
    status: str
    review_count: int
    required_reviews: int
    verdict_counts: Mapping[str, int]
    average_scores: Mapping[str, float]
    unresolved_findings: tuple[str, ...]


class AgentEvaluationEngine:
    """Persist independent reviews and calculate transparent quorum outcomes."""

    def __init__(
        self,
        database_path: str,
        *,
        registered_reviewer_ids: Iterable[str],
        authorize: Callable[[str, str, str], bool],
        required_reviews: int = 2,
        minimum_score: int = 3,
    ):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(authorize):
            raise TypeError("a trusted authorization callback is required.")
        reviewers = tuple(registered_reviewer_ids)
        if not reviewers or len(reviewers) > 256 or any(
            not isinstance(item, str) or not _ID.fullmatch(item) for item in reviewers
        ):
            raise ValueError("registered reviewer IDs must contain 1-256 bounded IDs.")
        if len(set(reviewers)) != len(reviewers):
            raise ValueError("reviewer IDs must be unique.")
        if type(required_reviews) is not int or not 2 <= required_reviews <= len(reviewers):
            raise ValueError("required_reviews must be between 2 and the reviewer count.")
        if type(minimum_score) is not int or not 1 <= minimum_score <= 5:
            raise ValueError("minimum_score must be between 1 and 5.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.reviewer_ids = frozenset(reviewers)
        self.authorize = authorize
        self.required_reviews = required_reviews
        self.minimum_score = minimum_score
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_id TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    scores_json TEXT NOT NULL,
                    findings_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(subject_id, reviewer_id)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_agent_reviews_subject ON agent_reviews(subject_id, review_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _allowed(self, actor: str, action: str, subject: str) -> bool:
        try:
            return self.authorize(actor, action, subject) is True
        except Exception:
            return False

    @staticmethod
    def _text(value: str, name: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise EvaluationError(f"{name} must be non-empty bounded text.")
        return value.strip()

    @staticmethod
    def _record(row: sqlite3.Row) -> AgentReview:
        return AgentReview(
            review_id=row["review_id"], subject_id=row["subject_id"],
            reviewer_id=row["reviewer_id"], verdict=row["verdict"],
            scores=json.loads(row["scores_json"]),
            findings=tuple(json.loads(row["findings_json"])),
            idempotency_key=row["idempotency_key"], created_at=row["created_at"],
        )

    def submit_review(
        self, *, actor_id: str, reviewer_id: str, subject_id: str,
        verdict: str, scores: Mapping[str, int], findings: Iterable[str],
        idempotency_key: str,
    ) -> AgentReview:
        actor = self._text(actor_id, "actor_id", 100)
        reviewer = self._text(reviewer_id, "reviewer_id", 100)
        subject = self._text(subject_id, "subject_id", 120)
        key = self._text(idempotency_key, "idempotency_key", 160)
        if actor != reviewer:
            raise PermissionError("reviewers may only submit as themselves.")
        if reviewer not in self.reviewer_ids:
            raise PermissionError("reviewer is not registered.")
        if not self._allowed(actor, "agent:review:submit", subject):
            raise PermissionError("review submission is not authorized.")
        if verdict not in _VERDICTS:
            raise EvaluationError("verdict must be approve, revise, or reject.")
        if not isinstance(scores, Mapping) or not 1 <= len(scores) <= 12:
            raise EvaluationError("scores must contain 1-12 rubric dimensions.")
        normalized_scores = {}
        for name, score in scores.items():
            if not isinstance(name, str) or not _ID.fullmatch(name):
                raise EvaluationError("rubric dimension names must be bounded identifiers.")
            if type(score) is not int or not 1 <= score <= 5:
                raise EvaluationError("rubric scores must be integers from 1 to 5.")
            normalized_scores[name] = score
        if not isinstance(findings, (list, tuple)):
            raise EvaluationError("findings must be a list or tuple of strings.")
        if len(findings) > _MAX_FINDINGS:
            raise EvaluationError("too many findings.")
        normalized_findings = tuple(self._text(item, "finding", 500) for item in findings)
        payload = {
            "subject_id": subject, "reviewer_id": reviewer, "verdict": verdict,
            "scores": normalized_scores, "findings": normalized_findings,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_REVIEW_BYTES:
            raise EvaluationError("review payload exceeds the size limit.")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM agent_reviews WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise EvaluationConflict("idempotency key was reused for a different review.")
                return self._record(existing)
            prior = db.execute(
                "SELECT review_id FROM agent_reviews WHERE subject_id=? AND reviewer_id=?",
                (subject, reviewer),
            ).fetchone()
            if prior:
                raise EvaluationConflict("reviewer has already reviewed this subject.")
            cur = db.execute("""
                INSERT INTO agent_reviews(
                    subject_id, reviewer_id, verdict, scores_json, findings_json,
                    idempotency_key, request_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                subject, reviewer, verdict,
                json.dumps(normalized_scores, sort_keys=True),
                json.dumps(normalized_findings), key, digest, now,
            ))
            row = db.execute("SELECT * FROM agent_reviews WHERE review_id=?", (cur.lastrowid,)).fetchone()
            return self._record(row)

    def consensus(self, *, actor_id: str, subject_id: str) -> ConsensusResult:
        actor = self._text(actor_id, "actor_id", 100)
        subject = self._text(subject_id, "subject_id", 120)
        if not self._allowed(actor, "agent:review:read", subject):
            raise PermissionError("review results are not authorized.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_reviews WHERE subject_id=? ORDER BY review_id",
                (subject,),
            ).fetchall()
        reviews = tuple(self._record(row) for row in rows)
        counts = {name: sum(r.verdict == name for r in reviews) for name in sorted(_VERDICTS)}
        if counts["reject"]:
            status = "rejected"
        elif len(reviews) < self.required_reviews:
            status = "awaiting_reviews"
        elif counts["revise"]:
            status = "revision_required"
        elif counts["approve"] >= self.required_reviews:
            status = "approved" if all(
                score >= self.minimum_score
                for review in reviews for score in review.scores.values()
            ) else "revision_required"
        else:
            status = "disagreement"
        dimensions = sorted({key for review in reviews for key in review.scores})
        averages = {
            key: round(sum(r.scores[key] for r in reviews if key in r.scores) /
                       sum(key in r.scores for r in reviews), 2)
            for key in dimensions
        }
        findings = tuple(
            f"{review.reviewer_id}: {finding}"
            for review in reviews if review.verdict != "approve"
            for finding in review.findings
        )
        return ConsensusResult(
            subject_id=subject, status=status, review_count=len(reviews),
            required_reviews=self.required_reviews, verdict_counts=counts,
            average_scores=averages, unresolved_findings=findings,
        )
