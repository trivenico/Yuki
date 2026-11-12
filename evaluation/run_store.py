"""Owner-scoped, append-only persistence for benchmark evaluation reports.

This store records already-completed evaluations. It never invokes providers,
changes model routing, or makes promotion decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Mapping

from evaluation.model_eval import EvalReport

_MAX_REPORT_BYTES = 512 * 1024
_MAX_ENVIRONMENT_BYTES = 8 * 1024
_MAX_OWNER_CHARS = 200
_MAX_IDEMPOTENCY_CHARS = 200
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_ENVIRONMENT_FIELDS = frozenset({
    "yuki_commit", "python_version", "platform", "architecture",
    "runtime_version", "dependency_lock_digest",
})


class EvaluationRunConflict(RuntimeError):
    """An idempotency key was reused for different evaluation content."""


class EvaluationRunInProgress(RuntimeError):
    """Another worker currently owns the idempotency key's live lease."""


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    run_id: str
    owner_id: str
    idempotency_key: str
    request_digest: str
    benchmark_id: str
    benchmark_version: int
    benchmark_digest: str
    candidate_id: str
    provider_id: str
    model_id: str
    environment: dict[str, str]
    report: dict[str, Any]
    report_digest: str
    record_digest: str
    created_at: str


def _canonical_json(value: Any, limit: int, field: str) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain finite JSON-compatible values") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError(f"{field} exceeds the {limit}-byte limit")
    return encoded


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvaluationRunStore:
    """SQLite journal with owner-scoped idempotency and immutable report records."""

    def __init__(self, database_path: str | Path):
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    run_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    benchmark_id TEXT NOT NULL,
                    benchmark_version INTEGER NOT NULL CHECK(benchmark_version > 0),
                    benchmark_digest TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    environment_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    report_digest TEXT NOT NULL,
                    record_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(owner_id, idempotency_key)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_evaluation_runs_owner_created "
                "ON evaluation_runs(owner_id, created_at DESC, run_id)"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_claims (
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, idempotency_key)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_evaluation_claims_expiry "
                "ON evaluation_claims(expires_at)"
            )

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return _ManagedConnection(db)

    @staticmethod
    def _required_text(value: str, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise ValueError(f"{field} must be non-empty bounded text")
        return value.strip()

    @staticmethod
    def _environment(value: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise TypeError("environment must be a mapping")
        if set(value) - _ENVIRONMENT_FIELDS:
            raise ValueError("environment contains unsupported fields")
        clean: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(item, str) or len(item) > 256 or "\x00" in item:
                raise ValueError("environment values must be bounded strings")
            if key == "dependency_lock_digest" and item and not _DIGEST_RE.fullmatch(item):
                raise ValueError("dependency_lock_digest must be a SHA-256 hex digest")
            if key == "yuki_commit" and item and not re.fullmatch(r"[a-f0-9]{7,64}", item):
                raise ValueError("yuki_commit must be a hexadecimal commit identifier")
            clean[key] = item
        _canonical_json(clean, _MAX_ENVIRONMENT_BYTES, "environment")
        return clean

    @staticmethod
    def _record_digest(fields: dict[str, Any]) -> str:
        return _digest(_canonical_json(fields, _MAX_REPORT_BYTES + _MAX_ENVIRONMENT_BYTES + 4096, "record"))

    @staticmethod
    def _from_row(row: sqlite3.Row) -> EvaluationRun:
        return EvaluationRun(
            run_id=row["run_id"], owner_id=row["owner_id"],
            idempotency_key=row["idempotency_key"], request_digest=row["request_digest"],
            benchmark_id=row["benchmark_id"],
            benchmark_version=row["benchmark_version"], benchmark_digest=row["benchmark_digest"],
            candidate_id=row["candidate_id"], provider_id=row["provider_id"],
            model_id=row["model_id"], environment=json.loads(row["environment_json"]),
            report=json.loads(row["report_json"]), report_digest=row["report_digest"],
            record_digest=row["record_digest"], created_at=row["created_at"],
        )

    @staticmethod
    def request_digest_for(
        *, benchmark_id: str, benchmark_version: int, benchmark_digest: str,
        candidate_id: str, provider_id: str, model_id: str,
        environment: Mapping[str, str],
    ) -> str:
        benchmark = EvaluationRunStore._required_text(benchmark_id, "benchmark_id", 100)
        candidate = EvaluationRunStore._required_text(candidate_id, "candidate_id", 200)
        provider = EvaluationRunStore._required_text(provider_id, "provider_id", 100)
        model = EvaluationRunStore._required_text(model_id, "model_id", 200)
        if type(benchmark_version) is not int or benchmark_version < 1:
            raise ValueError("benchmark_version must be a positive integer")
        if not isinstance(benchmark_digest, str) or not _DIGEST_RE.fullmatch(benchmark_digest):
            raise ValueError("benchmark_digest must be a SHA-256 hex digest")
        env = EvaluationRunStore._environment(environment)
        return EvaluationRunStore._record_digest({
            "benchmark_id": benchmark, "benchmark_version": benchmark_version,
            "benchmark_digest": benchmark_digest, "candidate_id": candidate,
            "provider_id": provider, "model_id": model, "environment": env,
        })

    def acquire_claim(
        self, *, owner_id: str, idempotency_key: str, request_digest: str,
        lease_seconds: int = 3600,
    ) -> str | None:
        """Atomically claim a run key; None means a matching completed run won the race."""
        owner = self._required_text(owner_id, "owner_id", _MAX_OWNER_CHARS)
        key = self._required_text(idempotency_key, "idempotency_key", _MAX_IDEMPOTENCY_CHARS)
        if not isinstance(request_digest, str) or not _DIGEST_RE.fullmatch(request_digest):
            raise ValueError("request_digest must be a SHA-256 hex digest")
        if type(lease_seconds) is not int or not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer between 30 and 3600")
        now = datetime.now(timezone.utc).timestamp()
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Reclaim a bounded batch of abandoned leases on normal write traffic.
            # This prevents unbounded accumulation without making each claim scan
            # or delete the entire expired-lease backlog.
            db.execute(
                "DELETE FROM evaluation_claims WHERE rowid IN ("
                "SELECT rowid FROM evaluation_claims WHERE expires_at <= ? "
                "ORDER BY expires_at LIMIT 100)",
                (now,),
            )
            completed = db.execute(
                "SELECT request_digest FROM evaluation_runs WHERE owner_id=? AND idempotency_key=?",
                (owner, key),
            ).fetchone()
            if completed is not None:
                if not hmac.compare_digest(completed["request_digest"], request_digest):
                    raise EvaluationRunConflict("idempotency key was used for different evaluation content")
                return None
            claim = db.execute(
                "SELECT request_digest, expires_at FROM evaluation_claims WHERE owner_id=? AND idempotency_key=?",
                (owner, key),
            ).fetchone()
            if claim is not None and claim["expires_at"] > now:
                if not hmac.compare_digest(claim["request_digest"], request_digest):
                    raise EvaluationRunConflict("idempotency key is claimed for different evaluation content")
                raise EvaluationRunInProgress("evaluation with this idempotency key is already running")
            db.execute(
                "INSERT INTO evaluation_claims(owner_id,idempotency_key,request_digest,lease_token,expires_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(owner_id,idempotency_key) DO UPDATE SET "
                "request_digest=excluded.request_digest, lease_token=excluded.lease_token, "
                "expires_at=excluded.expires_at",
                (owner, key, request_digest, token, now + lease_seconds),
            )
        return token

    def release_claim(self, *, owner_id: str, idempotency_key: str, lease_token: str) -> bool:
        owner = self._required_text(owner_id, "owner_id", _MAX_OWNER_CHARS)
        key = self._required_text(idempotency_key, "idempotency_key", _MAX_IDEMPOTENCY_CHARS)
        if not isinstance(lease_token, str) or not lease_token:
            raise ValueError("lease_token is required")
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM evaluation_claims WHERE owner_id=? AND idempotency_key=? AND lease_token=?",
                (owner, key, lease_token),
            )
            return cursor.rowcount == 1

    def record(
        self, *, owner_id: str, idempotency_key: str, benchmark_id: str,
        benchmark_version: int, benchmark_digest: str, candidate_id: str,
        provider_id: str, model_id: str, environment: Mapping[str, str],
        report: EvalReport,
        lease_token: str | None = None,
    ) -> EvaluationRun:
        owner = self._required_text(owner_id, "owner_id", _MAX_OWNER_CHARS)
        key = self._required_text(idempotency_key, "idempotency_key", _MAX_IDEMPOTENCY_CHARS)
        benchmark = self._required_text(benchmark_id, "benchmark_id", 100)
        candidate = self._required_text(candidate_id, "candidate_id", 200)
        provider = self._required_text(provider_id, "provider_id", 100)
        model = self._required_text(model_id, "model_id", 200)
        if type(benchmark_version) is not int or benchmark_version < 1:
            raise ValueError("benchmark_version must be a positive integer")
        if not isinstance(benchmark_digest, str) or not _DIGEST_RE.fullmatch(benchmark_digest):
            raise ValueError("benchmark_digest must be a SHA-256 hex digest")
        if not isinstance(report, EvalReport):
            raise TypeError("report must be an EvalReport")
        if report.candidate_id != candidate:
            raise ValueError("report candidate_id must match candidate_id")
        env = self._environment(environment)
        report_json = _canonical_json(report.to_dict(), _MAX_REPORT_BYTES, "report")
        environment_json = _canonical_json(env, _MAX_ENVIRONMENT_BYTES, "environment")
        report_digest = _digest(report_json)
        request_fields = {
            "benchmark_id": benchmark, "benchmark_version": benchmark_version,
            "benchmark_digest": benchmark_digest, "candidate_id": candidate,
            "provider_id": provider, "model_id": model, "environment": env,
        }
        request_digest = self._record_digest(request_fields)
        now = datetime.now(timezone.utc).isoformat()
        run_id = secrets.token_urlsafe(18)
        record_fields = {
            **request_fields, "run_id": run_id, "owner_id": owner,
            "idempotency_key": key, "request_digest": request_digest,
            "report": json.loads(report_json),
            "report_digest": report_digest, "created_at": now,
        }
        record_digest = self._record_digest(record_fields)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if lease_token is not None:
                claim = db.execute(
                    "SELECT request_digest, lease_token, expires_at FROM evaluation_claims "
                    "WHERE owner_id=? AND idempotency_key=?",
                    (owner, key),
                ).fetchone()
                if (claim is None or not hmac.compare_digest(claim["lease_token"], lease_token)
                    or claim["expires_at"] <= datetime.now(timezone.utc).timestamp()
                    or not hmac.compare_digest(claim["request_digest"], request_digest)):
                    raise EvaluationRunInProgress("evaluation lease is missing, expired, or no longer owned")
            old = db.execute(
                "SELECT * FROM evaluation_runs WHERE owner_id=? AND idempotency_key=?",
                (owner, key),
            ).fetchone()
            if old is not None:
                if not hmac.compare_digest(old["request_digest"], request_digest):
                    raise EvaluationRunConflict("idempotency key was used for different evaluation content")
                existing = self._from_row(old)
                if not self.verify(existing):
                    raise RuntimeError("stored evaluation record failed integrity verification")
                if lease_token is not None:
                    db.execute(
                        "DELETE FROM evaluation_claims WHERE owner_id=? AND idempotency_key=? AND lease_token=?",
                        (owner, key, lease_token),
                    )
                return existing
            db.execute("""
                INSERT INTO evaluation_runs (
                    run_id, owner_id, idempotency_key, request_digest,
                    benchmark_id, benchmark_version, benchmark_digest,
                    candidate_id, provider_id, model_id, environment_json,
                    report_json, report_digest, record_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, owner, key, request_digest, benchmark, benchmark_version,
                benchmark_digest, candidate, provider, model, environment_json,
                report_json, report_digest, record_digest, now,
            ))
            if lease_token is not None:
                db.execute(
                    "DELETE FROM evaluation_claims WHERE owner_id=? AND idempotency_key=? AND lease_token=?",
                    (owner, key, lease_token),
                )
            return self._from_row(db.execute(
                "SELECT * FROM evaluation_runs WHERE run_id=?", (run_id,)
            ).fetchone())

    def get(self, *, owner_id: str, run_id: str) -> EvaluationRun:
        owner = self._required_text(owner_id, "owner_id", _MAX_OWNER_CHARS)
        identifier = self._required_text(run_id, "run_id", 100)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM evaluation_runs WHERE owner_id=? AND run_id=?",
                (owner, identifier),
            ).fetchone()
        if row is None:
            raise KeyError("evaluation run not found")
        result = self._from_row(row)
        if not self.verify(result):
            raise RuntimeError("stored evaluation record failed integrity verification")
        return result

    def get_by_idempotency(self, *, owner_id: str, idempotency_key: str) -> EvaluationRun | None:
        """Return a verified owner-scoped result for a completed request key."""
        owner = self._required_text(owner_id, "owner_id", _MAX_OWNER_CHARS)
        key = self._required_text(idempotency_key, "idempotency_key", _MAX_IDEMPOTENCY_CHARS)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM evaluation_runs WHERE owner_id=? AND idempotency_key=?",
                (owner, key),
            ).fetchone()
        if row is None:
            return None
        result = self._from_row(row)
        if not self.verify(result):
            raise RuntimeError("stored evaluation record failed integrity verification")
        return result

    def list_runs(self, *, owner_id: str, limit: int = 100) -> tuple[EvaluationRun, ...]:
        owner = self._required_text(owner_id, "owner_id", _MAX_OWNER_CHARS)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM evaluation_runs WHERE owner_id=? "
                "ORDER BY created_at DESC, run_id LIMIT ?", (owner, limit)
            ).fetchall()
        records = tuple(self._from_row(row) for row in rows)
        if any(not self.verify(record) for record in records):
            raise RuntimeError("stored evaluation record failed integrity verification")
        return records

    @staticmethod
    def verify(run: EvaluationRun) -> bool:
        if not isinstance(run, EvaluationRun):
            return False
        report_json = _canonical_json(run.report, _MAX_REPORT_BYTES, "report")
        if not hmac.compare_digest(_digest(report_json), run.report_digest):
            return False
        fields = {
            "benchmark_id": run.benchmark_id,
            "benchmark_version": run.benchmark_version,
            "benchmark_digest": run.benchmark_digest,
            "candidate_id": run.candidate_id,
            "provider_id": run.provider_id,
            "model_id": run.model_id,
            "environment": run.environment,
            "report_digest": run.report_digest,
            "run_id": run.run_id,
            "owner_id": run.owner_id,
            "idempotency_key": run.idempotency_key,
            "request_digest": run.request_digest,
            "report": run.report,
            "report_digest": run.report_digest,
            "created_at": run.created_at,
        }
        return hmac.compare_digest(
            EvaluationRunStore._record_digest(fields), run.record_digest
        )


class _ManagedConnection:
    """Small context manager ensuring sqlite connections are always closed."""

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
