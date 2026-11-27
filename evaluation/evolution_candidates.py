"""Owner-scoped, proposal-only registry for self-evolution candidates.

The registry stores bounded declarative proposals and review events. It does not
execute candidate content, invoke models/tools, evaluate candidates, or promote
runtime configuration. Caller-supplied owner/reviewer identifiers are labels,
not authentication.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Mapping

from core.sqlite_file_security import prepare_private_sqlite_path

_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_EVIDENCE = 32
_MAX_TAGS = 24
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ALLOWED_KINDS = frozenset({"preference", "workflow", "prompt", "evaluation", "safety"})
_ALLOWED_REVIEW_STATES = frozenset({"proposed", "needs_evidence", "rejected", "accepted_for_evaluation"})


@dataclass(frozen=True, slots=True)
class EvolutionCandidate:
    candidate_id: str
    owner_id: str
    kind: str
    title: str
    rationale: str
    expected_outcome: str
    evidence_refs: tuple[str, ...]
    risk_tags: tuple[str, ...]
    parent_digest: str | None
    payload_digest: str
    created_at: str
    state: str
    revision: int


def _canonical(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate fields must be finite JSON-compatible values") from exc
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("candidate payload exceeds the 65536-byte limit")
    return encoded


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be non-empty bounded text")
    return value.strip()


def _refs(value: tuple[str, ...] | list[str], field: str, limit: int) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > limit:
        raise ValueError(f"{field} must be a bounded list")
    clean = tuple(_text(item, field, 256) for item in value)
    if len(set(clean)) != len(clean):
        raise ValueError(f"{field} entries must be unique")
    return clean


@dataclass(frozen=True, slots=True)
class CandidateEvaluationEvidence:
    candidate_id: str
    candidate_digest: str
    run_id: str
    run_record_digest: str
    report_digest: str
    benchmark_id: str
    benchmark_version: int
    benchmark_digest: str
    linked_at: str


class EvolutionCandidateRegistry:
    """SQLite-backed immutable candidate content with append-only review events."""

    def __init__(self, database_path: str | Path, *, max_candidates: int = 2000):
        if type(max_candidates) is not int or not 1 <= max_candidates <= 100_000:
            raise ValueError("max_candidates must be an integer between 1 and 100000")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.max_candidates = max_candidates
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS evolution_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_evolution_candidates_owner "
                       "ON evolution_candidates(owner_id, created_at DESC)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS evolution_candidate_evaluations (
                    candidate_id TEXT NOT NULL REFERENCES evolution_candidates(candidate_id),
                    owner_id TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    run_record_digest TEXT NOT NULL,
                    report_digest TEXT NOT NULL,
                    benchmark_id TEXT NOT NULL,
                    benchmark_version INTEGER NOT NULL CHECK(benchmark_version > 0),
                    benchmark_digest TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id, candidate_id, run_id),
                    UNIQUE(owner_id, run_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS evolution_candidate_events (
                    event_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES evolution_candidates(candidate_id),
                    owner_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        self.database_path = prepare_private_sqlite_path(self.database_path)
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _candidate(row: sqlite3.Row, state: str, revision: int) -> EvolutionCandidate:
        payload = json.loads(row["payload_json"])
        if _digest(_canonical(payload)) != row["payload_digest"]:
            raise RuntimeError("evolution candidate integrity verification failed")
        return EvolutionCandidate(
            candidate_id=row["candidate_id"], owner_id=row["owner_id"],
            kind=payload["kind"], title=payload["title"],
            rationale=payload["rationale"], expected_outcome=payload["expected_outcome"],
            evidence_refs=tuple(payload["evidence_refs"]), risk_tags=tuple(payload["risk_tags"]),
            parent_digest=payload["parent_digest"], payload_digest=row["payload_digest"],
            created_at=row["created_at"], state=state, revision=revision,
        )

    def propose(
        self, *, owner_id: str, kind: str, title: str, rationale: str,
        expected_outcome: str, evidence_refs: tuple[str, ...] | list[str] = (),
        risk_tags: tuple[str, ...] | list[str] = (), parent_digest: str | None = None,
        candidate_id: str | None = None,
    ) -> EvolutionCandidate:
        owner = _text(owner_id, "owner_id", 200)
        if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
            raise ValueError("kind must be preference, workflow, prompt, evaluation, or safety")
        title = _text(title, "title", 160)
        rationale = _text(rationale, "rationale", 4000)
        expected_outcome = _text(expected_outcome, "expected_outcome", 1000)
        refs = _refs(evidence_refs, "evidence_refs", _MAX_EVIDENCE)
        tags = _refs(risk_tags, "risk_tags", _MAX_TAGS)
        if parent_digest is not None and (
            not isinstance(parent_digest, str) or not _DIGEST_RE.fullmatch(parent_digest)
        ):
            raise ValueError("parent_digest must be a SHA-256 hex digest or None")
        identifier = candidate_id or secrets.token_urlsafe(18)
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            raise ValueError("candidate_id must be a bounded identifier")
        payload = {
            "schema_version": 1, "kind": kind, "title": title,
            "rationale": rationale, "expected_outcome": expected_outcome,
            "evidence_refs": list(refs), "risk_tags": list(tags),
            "parent_digest": parent_digest,
        }
        payload_json = _canonical(payload)
        payload_digest = _digest(payload_json)
        created = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) AS n FROM evolution_candidates").fetchone()["n"]
            if count >= self.max_candidates:
                raise ValueError("evolution candidate registry capacity reached")
            try:
                db.execute(
                    "INSERT INTO evolution_candidates "
                    "(candidate_id,owner_id,payload_json,payload_digest,created_at) VALUES(?,?,?,?,?)",
                    (identifier, owner, payload_json, payload_digest, created),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("candidate_id already exists") from exc
        return EvolutionCandidate(identifier, owner, kind, title, rationale,
                                  expected_outcome, refs, tags, parent_digest,
                                  payload_digest, created, "proposed", 0)

    def get(self, *, owner_id: str, candidate_id: str) -> EvolutionCandidate:
        owner = _text(owner_id, "owner_id", 200)
        identifier = _text(candidate_id, "candidate_id", 128)
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM evolution_candidates WHERE owner_id=? AND candidate_id=?",
                (owner, identifier),
            ).fetchone()
            if row is None:
                raise KeyError("evolution candidate not found")
            events = db.execute(
                "SELECT from_state,to_state,payload_digest FROM evolution_candidate_events "
                "WHERE owner_id=? AND candidate_id=? ORDER BY rowid",
                (owner, identifier),
            ).fetchall()
        state, revision = "proposed", 0
        for event in events:
            if (event["from_state"] != state or event["to_state"] not in _ALLOWED_REVIEW_STATES
                    or event["payload_digest"] != row["payload_digest"]):
                raise RuntimeError("evolution candidate review history failed integrity verification")
            state = event["to_state"]
            revision += 1
        return self._candidate(row, state, revision)

    def review(
        self, *, owner_id: str, candidate_id: str, expected_digest: str,
        expected_revision: int, reviewer_id: str, decision: str, note: str = "",
    ) -> EvolutionCandidate:
        owner = _text(owner_id, "owner_id", 200)
        identifier = _text(candidate_id, "candidate_id", 128)
        reviewer = _text(reviewer_id, "reviewer_id", 200)
        note = note if isinstance(note, str) and len(note) <= 1000 and "\x00" not in note else None
        if note is None:
            raise ValueError("note must be bounded text")
        if not isinstance(expected_digest, str) or not _DIGEST_RE.fullmatch(expected_digest):
            raise ValueError("expected_digest must be a SHA-256 hex digest")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if not isinstance(decision, str) or decision not in {"needs_evidence", "rejected", "accepted_for_evaluation"}:
            raise ValueError("decision must be needs_evidence, rejected, or accepted_for_evaluation")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM evolution_candidates WHERE owner_id=? AND candidate_id=?",
                (owner, identifier),
            ).fetchone()
            if row is None:
                raise KeyError("evolution candidate not found")
            events = db.execute(
                "SELECT from_state,to_state,payload_digest FROM evolution_candidate_events "
                "WHERE owner_id=? AND candidate_id=? ORDER BY rowid",
                (owner, identifier),
            ).fetchall()
            state, revision = "proposed", 0
            for event in events:
                if (event["from_state"] != state or event["to_state"] not in _ALLOWED_REVIEW_STATES
                    or event["payload_digest"] != row["payload_digest"]):
                    raise RuntimeError("evolution candidate review history failed integrity verification")
                state, revision = event["to_state"], revision + 1
            if row["payload_digest"] != expected_digest or revision != expected_revision:
                raise ValueError("candidate changed since it was reviewed; refresh before retrying")
            if state not in {"proposed", "needs_evidence"}:
                raise ValueError("candidate is not in a reviewable state")
            db.execute(
                "INSERT INTO evolution_candidate_events "
                "(event_id,candidate_id,owner_id,from_state,to_state,reviewer_id,note,payload_digest,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (secrets.token_urlsafe(18), identifier, owner, state, decision,
                 reviewer, note, expected_digest, datetime.now(timezone.utc).isoformat()),
            )
        return self.get(owner_id=owner, candidate_id=identifier)


    def attach_evaluation(
        self, *, owner_id: str, candidate_id: str, expected_digest: str,
        run_id: str, run_store: Any,
    ) -> CandidateEvaluationEvidence:
        """Bind a verified completed run to the exact reviewed candidate digest."""
        owner = _text(owner_id, "owner_id", 200)
        identifier = _text(candidate_id, "candidate_id", 128)
        run_identifier = _text(run_id, "run_id", 100)
        if not isinstance(expected_digest, str) or not _DIGEST_RE.fullmatch(expected_digest):
            raise ValueError("expected_digest must be a SHA-256 hex digest")
        if not callable(getattr(run_store, "get", None)) or not callable(getattr(run_store, "verify", None)):
            raise TypeError("run_store must provide get and verify methods")
        candidate = self.get(owner_id=owner, candidate_id=identifier)
        if candidate.state != "accepted_for_evaluation":
            raise ValueError("candidate must be accepted_for_evaluation before attaching a run")
        if candidate.payload_digest != expected_digest:
            raise ValueError("candidate digest does not match the reviewed artifact")
        run = run_store.get(owner_id=owner, run_id=run_identifier)
        if not run_store.verify(run):
            raise ValueError("evaluation run failed integrity verification")
        if run.owner_id != owner or run.candidate_id != identifier:
            raise ValueError("evaluation run owner or candidate does not match")
        if run.candidate_digest != expected_digest:
            raise ValueError("evaluation run is not cryptographically bound to this candidate digest")
        if not _DIGEST_RE.fullmatch(run.report_digest) or not _DIGEST_RE.fullmatch(run.record_digest):
            raise ValueError("evaluation run is missing valid integrity digests")
        linked_at = datetime.now(timezone.utc).isoformat()
        evidence = CandidateEvaluationEvidence(
            candidate_id=identifier, candidate_digest=expected_digest,
            run_id=run.run_id, run_record_digest=run.record_digest,
            report_digest=run.report_digest, benchmark_id=run.benchmark_id,
            benchmark_version=run.benchmark_version,
            benchmark_digest=run.benchmark_digest, linked_at=linked_at,
        )
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT payload_digest FROM evolution_candidates WHERE owner_id=? AND candidate_id=?",
                (owner, identifier),
            ).fetchone()
            if current is None or current["payload_digest"] != expected_digest:
                raise ValueError("candidate changed before evaluation evidence was linked")
            events = db.execute(
                "SELECT from_state,to_state,payload_digest FROM evolution_candidate_events "
                "WHERE owner_id=? AND candidate_id=? ORDER BY rowid", (owner, identifier),
            ).fetchall()
            state = "proposed"
            for event in events:
                if (event["from_state"] != state or event["to_state"] not in _ALLOWED_REVIEW_STATES
                    or event["payload_digest"] != expected_digest):
                    raise RuntimeError("candidate review history failed integrity verification")
                state = event["to_state"]
            if state != "accepted_for_evaluation":
                raise ValueError("candidate is no longer accepted for evaluation")
            try:
                db.execute(
                    "INSERT INTO evolution_candidate_evaluations "
                    "(candidate_id,owner_id,candidate_digest,run_id,run_record_digest,report_digest,"
                    "benchmark_id,benchmark_version,benchmark_digest,linked_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (identifier, owner, expected_digest, run.run_id, run.record_digest,
                     run.report_digest, run.benchmark_id, run.benchmark_version,
                     run.benchmark_digest, linked_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("evaluation run is already linked to this owner/candidate") from exc
        return evidence

    def list_evaluations(
        self, *, owner_id: str, candidate_id: str, run_store: Any,
    ) -> tuple[CandidateEvaluationEvidence, ...]:
        """Return links only while their referenced immutable evaluation runs verify."""
        owner = _text(owner_id, "owner_id", 200)
        identifier = _text(candidate_id, "candidate_id", 128)
        if not callable(getattr(run_store, "get", None)) or not callable(getattr(run_store, "verify", None)):
            raise TypeError("run_store must provide get and verify methods")
        candidate = self.get(owner_id=owner, candidate_id=identifier)
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT * FROM evolution_candidate_evaluations WHERE owner_id=? AND candidate_id=? "
                "ORDER BY linked_at, run_id", (owner, identifier),
            ).fetchall()
        evidence = []
        for row in rows:
            if row["candidate_digest"] != candidate.payload_digest:
                raise RuntimeError("linked evaluation candidate digest failed integrity verification")
            run = run_store.get(owner_id=owner, run_id=row["run_id"])
            if (not run_store.verify(run) or run.candidate_id != identifier
                or run.candidate_digest != row["candidate_digest"]
                or run.record_digest != row["run_record_digest"]
                or run.report_digest != row["report_digest"]
                or run.benchmark_id != row["benchmark_id"]
                or run.benchmark_version != row["benchmark_version"]
                or run.benchmark_digest != row["benchmark_digest"]):
                raise RuntimeError("linked evaluation evidence failed integrity verification")
            evidence.append(CandidateEvaluationEvidence(
                candidate_id=identifier, candidate_digest=row["candidate_digest"],
                run_id=row["run_id"], run_record_digest=row["run_record_digest"],
                report_digest=row["report_digest"], benchmark_id=row["benchmark_id"],
                benchmark_version=row["benchmark_version"], benchmark_digest=row["benchmark_digest"],
                linked_at=row["linked_at"],
            ))
        return tuple(evidence)
