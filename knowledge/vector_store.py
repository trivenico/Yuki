"""Small SQLite vector index for local semantic retrieval."""
from __future__ import annotations

import math
import sqlite3
import struct
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Sequence


class VectorStore:
    MAX_DIMENSIONS = 8192
    MAX_BATCH = 512

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_vectors (
                    chunk_id INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    PRIMARY KEY(chunk_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_vectors_source
                    ON knowledge_vectors(source_id);
            """)

    def _connect(self):
        db = sqlite3.connect(str(self.database_path), timeout=10.0)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _normalize(vector: Sequence[float]) -> list[float]:
        if not isinstance(vector, (list, tuple)) or not vector:
            raise ValueError("vector must be a non-empty sequence.")
        if len(vector) > VectorStore.MAX_DIMENSIONS:
            raise ValueError("vector exceeds the maximum dimension.")
        values = [float(value) for value in vector]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("vector values must be finite.")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 1e-12:
            raise ValueError("zero vectors are not supported.")
        return [value / norm for value in values]

    def upsert(self, *, chunk_id: int, source_id: str, model_id: str,
               vector: Sequence[float]) -> None:
        if type(chunk_id) is not int or chunk_id <= 0:
            raise ValueError("chunk_id must be a positive integer.")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id is required.")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 300:
            raise ValueError("model_id must be non-empty text of at most 300 characters.")
        values = self._normalize(vector)
        blob = struct.pack(f"<{len(values)}f", *values)
        with self._lock, self._session() as db:
            db.execute(
                """INSERT INTO knowledge_vectors(chunk_id, source_id, model_id, dimensions, vector)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chunk_id, model_id) DO UPDATE SET
                     source_id=excluded.source_id, dimensions=excluded.dimensions,
                     vector=excluded.vector""",
                (chunk_id, source_id, model_id, len(values), blob),
            )

    def upsert_many(self, records: Sequence[dict], *, model_id: str) -> int:
        if not isinstance(records, (list, tuple)) or not records or len(records) > self.MAX_BATCH:
            raise ValueError(f"records must contain 1-{self.MAX_BATCH} items.")
        prepared = []
        for item in records:
            if not isinstance(item, dict):
                raise ValueError("Each record must be a mapping.")
            vector = self._normalize(item.get("vector"))
            chunk_id, source_id = item.get("chunk_id"), item.get("source_id")
            if type(chunk_id) is not int or chunk_id <= 0:
                raise ValueError("chunk_id must be a positive integer.")
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError("source_id is required.")
            prepared.append((chunk_id, source_id, model_id, len(vector),
                             struct.pack(f"<{len(vector)}f", *vector)))
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 300:
            raise ValueError("model_id must be non-empty text of at most 300 characters.")
        dimensions = {item[3] for item in prepared}
        if len(dimensions) != 1:
            raise ValueError("All vectors in a batch must have identical dimensions.")
        with self._lock, self._connect() as db:
            db.executemany(
                """INSERT INTO knowledge_vectors(chunk_id, source_id, model_id, dimensions, vector)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chunk_id, model_id) DO UPDATE SET
                     source_id=excluded.source_id, dimensions=excluded.dimensions,
                     vector=excluded.vector""", prepared,
            )
        return len(prepared)

    def search(self, vector: Sequence[float], *, model_id: str, limit: int = 5) -> list[dict]:
        query = self._normalize(vector)
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        with self._lock, self._connect() as db:
            rows = db.execute(
                """SELECT chunk_id, source_id, dimensions, vector
                   FROM knowledge_vectors WHERE model_id=? AND dimensions=?""",
                (model_id, len(query)),
            ).fetchall()
        matches = []
        for chunk_id, source_id, dimensions, blob in rows:
            stored = struct.unpack(f"<{dimensions}f", blob)
            score = sum(a * b for a, b in zip(query, stored))
            matches.append({"chunk_id": chunk_id, "source_id": source_id, "score": float(score)})
        matches.sort(key=lambda item: (-item["score"], item["chunk_id"]))
        return matches[:limit]

    def prune_source(self, source_id: str, keep_chunk_ids: Sequence[int], *, model_id: str) -> int:
        """Remove stale vectors for a source after replacement vectors are safely stored."""
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id is required.")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 300:
            raise ValueError("model_id must be non-empty text of at most 300 characters.")
        if not isinstance(keep_chunk_ids, (list, tuple)) or len(keep_chunk_ids) > 10000:
            raise ValueError("keep_chunk_ids must contain at most 10000 IDs.")
        if any(type(item) is not int or item <= 0 for item in keep_chunk_ids):
            raise ValueError("chunk IDs must be positive integers.")
        keep = sorted(set(keep_chunk_ids))
        with self._lock, self._connect() as db:
            if not keep:
                cursor = db.execute(
                    "DELETE FROM knowledge_vectors WHERE source_id=? AND model_id=?",
                    (source_id, model_id),
                )
            else:
                placeholders = ",".join("?" for _ in keep)
                cursor = db.execute(
                    f"DELETE FROM knowledge_vectors WHERE source_id=? AND model_id=? "
                    f"AND chunk_id NOT IN ({placeholders})",
                    (source_id, model_id, *keep),
                )
            return cursor.rowcount

    def delete_source(self, source_id: str, *, model_id: str | None = None) -> int:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id is required.")
        with self._lock, self._connect() as db:
            if model_id is None:
                cursor = db.execute("DELETE FROM knowledge_vectors WHERE source_id=?", (source_id,))
            else:
                cursor = db.execute(
                    "DELETE FROM knowledge_vectors WHERE source_id=? AND model_id=?",
                    (source_id, model_id),
                )
            return cursor.rowcount
