"""Immutable, content-addressed benchmark definitions with optional SQLite durability."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from contextlib import closing
import json
import re
import sqlite3
from pathlib import Path
from threading import RLock

from evaluation.model_eval import EvalCase
from core.sqlite_file_security import prepare_private_sqlite_path

_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class BenchmarkDefinition:
    benchmark_id: str
    version: int
    name: str
    cases: tuple[EvalCase, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark_id, str) or not _ID.fullmatch(self.benchmark_id):
            raise ValueError("benchmark_id must be a bounded identifier")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 160:
            raise ValueError("name must be non-empty bounded text")
        if not isinstance(self.description, str) or len(self.description) > 1000:
            raise ValueError("description must be bounded text")
        if not isinstance(self.cases, tuple) or not 1 <= len(self.cases) <= 200:
            raise ValueError("cases must be a tuple containing 1..200 cases")
        if any(not isinstance(case, EvalCase) for case in self.cases):
            raise TypeError("cases must contain EvalCase instances")
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("benchmark case IDs must be unique")
        for case in self.cases:
            ModelCaseValidator.validate(case)

    def canonical_payload(self) -> str:
        payload = {
            "benchmark_id": self.benchmark_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "cases": [{
                "case_id": case.case_id,
                "task": case.task,
                "prompt": case.prompt,
                "required_terms": list(case.required_terms),
                "forbidden_terms": list(case.forbidden_terms),
                "max_latency_seconds": case.max_latency_seconds,
                "max_response_chars": case.max_response_chars,
            } for case in self.cases],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, payload: str) -> "BenchmarkDefinition":
        try:
            value = json.loads(payload)
            if not isinstance(value, dict) or set(value) != {
                "benchmark_id", "version", "name", "description", "cases"
            } or not isinstance(value["cases"], list):
                raise ValueError("invalid benchmark payload")
            cases = []
            for item in value["cases"]:
                if not isinstance(item, dict) or set(item) != {
                    "case_id", "task", "prompt", "required_terms",
                    "forbidden_terms", "max_latency_seconds", "max_response_chars"
                }:
                    raise ValueError("invalid benchmark case payload")
                cases.append(EvalCase(
                    case_id=item["case_id"], task=item["task"], prompt=item["prompt"],
                    required_terms=tuple(item["required_terms"]),
                    forbidden_terms=tuple(item["forbidden_terms"]),
                    max_latency_seconds=item["max_latency_seconds"],
                    max_response_chars=item["max_response_chars"],
                ))
            return cls(
                benchmark_id=value["benchmark_id"], version=value["version"],
                name=value["name"], description=value["description"], cases=tuple(cases),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid stored benchmark definition") from exc


class ModelCaseValidator:
    @staticmethod
    def validate(case: EvalCase) -> None:
        from evaluation.model_eval import ModelEvaluator
        ModelEvaluator._validate_case(case)
        if len(case.prompt) > 8_000:
            raise ValueError("benchmark prompt exceeds the default 8000-character limit")


class BenchmarkRegistry:
    """Thread-safe immutable registry; pass a database path for restart durability."""

    def __init__(self, *, max_revisions: int = 500, database_path: str | Path | None = None) -> None:
        if type(max_revisions) is not int or not 1 <= max_revisions <= 10_000:
            raise ValueError("max_revisions must be an integer between 1 and 10000")
        if database_path is not None and (
            not isinstance(database_path, (str, Path)) or not str(database_path).strip()
        ):
            raise ValueError("database_path must be a non-empty path")
        self._max_revisions = max_revisions
        self.database_path = prepare_private_sqlite_path(database_path) if database_path is not None else None
        self._definitions: dict[tuple[str, int], BenchmarkDefinition] = {}
        self._lock = RLock()
        if self.database_path is not None:
            with closing(self._connect()) as db, db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS benchmark_revisions (
                        benchmark_id TEXT NOT NULL,
                        version INTEGER NOT NULL CHECK(version > 0),
                        digest TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY(benchmark_id, version)
                    )
                """)
                rows = db.execute(
                    "SELECT benchmark_id, version, digest, payload FROM benchmark_revisions "
                    "ORDER BY benchmark_id, version"
                ).fetchall()
            for row in rows:
                try:
                    definition = BenchmarkDefinition.from_payload(row["payload"])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("stored benchmark revision failed integrity verification") from exc
                if (definition.benchmark_id != row["benchmark_id"]
                    or definition.version != row["version"]
                    or not _DIGEST.fullmatch(row["digest"])
                    or definition.digest != row["digest"]):
                    raise RuntimeError("stored benchmark revision failed integrity verification")
                key = (definition.benchmark_id, definition.version)
                versions = [v for bid, v in self._definitions if bid == definition.benchmark_id]
                if definition.version != (max(versions) + 1 if versions else 1):
                    raise RuntimeError("stored benchmark revision sequence is inconsistent")
                self._definitions[key] = definition
            if len(self._definitions) > self._max_revisions:
                raise RuntimeError("stored benchmark revisions exceed configured capacity")

    def _connect(self):
        if self.database_path is None:
            raise RuntimeError("persistent database path is not configured")
        # Re-check the final path before every SQLite open.
        self.database_path = prepare_private_sqlite_path(self.database_path)
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _refresh_from_database(self) -> None:
        if self.database_path is None:
            return
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT benchmark_id, version, digest, payload FROM benchmark_revisions "
                "ORDER BY benchmark_id, version"
            ).fetchall()
        refreshed: dict[tuple[str, int], BenchmarkDefinition] = {}
        for row in rows:
            try:
                definition = BenchmarkDefinition.from_payload(row["payload"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("stored benchmark revision failed integrity verification") from exc
            if (definition.benchmark_id != row["benchmark_id"]
                or definition.version != row["version"]
                or not _DIGEST.fullmatch(row["digest"])
                or definition.digest != row["digest"]):
                raise RuntimeError("stored benchmark revision failed integrity verification")
            key = (definition.benchmark_id, definition.version)
            versions = [version for benchmark_id, version in refreshed if benchmark_id == definition.benchmark_id]
            if definition.version != (max(versions) + 1 if versions else 1):
                raise RuntimeError("stored benchmark revision sequence is inconsistent")
            refreshed[key] = definition
        if len(refreshed) > self._max_revisions:
            raise RuntimeError("stored benchmark revisions exceed configured capacity")
        self._definitions = refreshed

    def register(self, definition: BenchmarkDefinition) -> BenchmarkDefinition:
        if not isinstance(definition, BenchmarkDefinition):
            raise TypeError("definition must be a BenchmarkDefinition")
        key = (definition.benchmark_id, definition.version)
        with self._lock:
            self._refresh_from_database()
            existing = self._definitions.get(key)
            if existing is not None:
                if existing.digest != definition.digest:
                    raise ValueError("benchmark revisions are immutable; increment version for changes")
                return existing
            versions = [v for bid, v in self._definitions if bid == definition.benchmark_id]
            if definition.version != (max(versions) + 1 if versions else 1):
                raise ValueError("new benchmark revisions must increment the latest version by one")
            if len(self._definitions) >= self._max_revisions:
                raise ValueError("benchmark registry capacity reached")
            if self.database_path is not None:
                with closing(self._connect()) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute(
                        "SELECT digest, payload FROM benchmark_revisions WHERE benchmark_id=? AND version=?",
                        key,
                    ).fetchone()
                    if row is not None:
                        if row["digest"] != definition.digest or row["payload"] != definition.canonical_payload():
                            raise ValueError("benchmark revisions are immutable; increment version for changes")
                    else:
                        count = db.execute(
                            "SELECT COUNT(*) AS count FROM benchmark_revisions"
                        ).fetchone()["count"]
                        if count >= self._max_revisions:
                            raise ValueError("benchmark registry capacity reached")
                        latest = db.execute(
                            "SELECT MAX(version) AS version FROM benchmark_revisions WHERE benchmark_id=?",
                            (definition.benchmark_id,),
                        ).fetchone()["version"]
                        expected = (latest + 1) if latest is not None else 1
                        if definition.version != expected:
                            raise ValueError("new benchmark revisions must increment the latest version by one")
                        db.execute(
                            "INSERT INTO benchmark_revisions(benchmark_id, version, digest, payload) VALUES (?, ?, ?, ?)",
                            (definition.benchmark_id, definition.version, definition.digest, definition.canonical_payload()),
                        )
            self._definitions[key] = definition
            return definition

    def resolve(self, benchmark_id: str, version: int, *, expected_digest: str | None = None) -> BenchmarkDefinition:
        if not isinstance(benchmark_id, str) or type(version) is not int:
            raise ValueError("benchmark ID and integer version are required")
        with self._lock:
            self._refresh_from_database()
            definition = self._definitions.get((benchmark_id, version))
            if definition is None:
                raise KeyError("benchmark revision not found")
            if expected_digest is not None:
                if not isinstance(expected_digest, str) or not _DIGEST.fullmatch(expected_digest):
                    raise ValueError("expected_digest must be a SHA-256 hex digest")
                if definition.digest != expected_digest:
                    raise ValueError("benchmark digest does not match requested revision")
            return definition

    def list_revisions(self, benchmark_id: str) -> tuple[tuple[int, str], ...]:
        if not isinstance(benchmark_id, str) or not _ID.fullmatch(benchmark_id):
            raise ValueError("benchmark_id must be a bounded identifier")
        with self._lock:
            self._refresh_from_database()
            return tuple(
                (version, self._definitions[(benchmark_id, version)].digest)
                for version in sorted(v for key, v in self._definitions if key == benchmark_id)
            )
