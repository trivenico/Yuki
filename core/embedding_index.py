"""Persistent, model-versioned SQLite cache for local memory embeddings."""
from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
from pathlib import Path
from threading import RLock
from typing import Sequence


class SQLiteEmbeddingIndex:
    MAX_ABS_COMPONENT = 1_000_000.0
    """Cache vectors by actor, memory, content digest, and model identity.

    The authoritative memory store still controls which records are visible.
    This index is only consulted for memory IDs returned by that authorized
    store query, so cached vectors cannot broaden memory visibility.
    """

    def __init__(self, database_path: str, *, model_id: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
            raise ValueError("embedding_model_id_required")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id.strip()
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS agent_memory_embeddings (
                actor_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                dimensions INTEGER NOT NULL CHECK(dimensions BETWEEN 1 AND 16384),
                vector BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_id, memory_id, model_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model
                ON agent_memory_embeddings(model_id, actor_id)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return _ClosingConnection(db)

    @staticmethod
    def _digest(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _pack(vector: Sequence[float]) -> bytes:
        if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
            raise ValueError("embedding_vector_must_be_sequence")
        if not 1 <= len(vector) <= 16384:
            raise ValueError("embedding_dimension_out_of_range")
        values = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("embedding_vector_must_be_finite")
            try:
                normalized = float(value)
            except (OverflowError, ValueError) as exc:
                raise ValueError("embedding_vector_must_be_finite") from exc
            if not math.isfinite(normalized) or abs(normalized) > SQLiteEmbeddingIndex.MAX_ABS_COMPONENT:
                raise ValueError("embedding_vector_component_out_of_range")
            values.append(normalized)
        try:
            packed = struct.pack(f"<{len(values)}f", *values)
        except (OverflowError, struct.error) as exc:
            raise ValueError("embedding_vector_cannot_be_packed") from exc
        stored = struct.unpack(f"<{len(values)}f", packed)
        if not any(stored):
            raise ValueError("embedding_vector_must_be_nonzero_after_storage")
        return packed

    @staticmethod
    def _unpack(blob: bytes, dimensions: int) -> list[float]:
        if type(dimensions) is not int or not 1 <= dimensions <= 16384:
            raise ValueError("stored_embedding_dimension_invalid")
        if not isinstance(blob, bytes) or len(blob) != dimensions * 4:
            raise ValueError("stored_embedding_blob_invalid")
        vector = list(struct.unpack(f"<{dimensions}f", blob))
        if not all(
            math.isfinite(value) and abs(value) <= SQLiteEmbeddingIndex.MAX_ABS_COMPONENT
            for value in vector
        ):
            raise ValueError("stored_embedding_contains_out_of_range_values")
        return vector

    def get_many(self, *, actor_id: str, memories: list[dict]) -> dict[str, list[float]]:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id_required")
        if not isinstance(memories, list) or len(memories) > 100:
            raise ValueError("memories_must_be_bounded_list")
        if not memories:
            return {}
        expected = {}
        for memory in memories:
            if not isinstance(memory, dict):
                raise ValueError("invalid_memory_record")
            memory_id, content = memory.get("memory_id"), memory.get("content")
            if not isinstance(memory_id, str) or not memory_id or not isinstance(content, str):
                raise ValueError("memory_id_and_content_required")
            expected[memory_id] = self._digest(content)
        placeholders = ",".join("?" for _ in expected)
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT memory_id,content_sha256,dimensions,vector
                    FROM agent_memory_embeddings
                    WHERE actor_id=? AND model_id=? AND memory_id IN ({placeholders})""",
                (actor_id, self.model_id, *expected.keys()),
            ).fetchall()
        result = {}
        for memory_id, digest, dimensions, blob in rows:
            if expected.get(memory_id) != digest:
                continue
            result[memory_id] = self._unpack(blob, dimensions)
        return result

    def put_many(self, *, actor_id: str, memories: list[dict],
                 vectors: list[Sequence[float]]) -> int:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id_required")
        if not isinstance(memories, list) or not isinstance(vectors, list) or len(memories) != len(vectors):
            raise ValueError("memory_vector_batch_mismatch")
        if len(memories) > 100:
            raise ValueError("embedding_batch_exceeds_100")
        rows = []
        dimension = None
        for memory, vector in zip(memories, vectors):
            if not isinstance(memory, dict):
                raise ValueError("invalid_memory_record")
            memory_id, content = memory.get("memory_id"), memory.get("content")
            if not isinstance(memory_id, str) or not memory_id or not isinstance(content, str):
                raise ValueError("memory_id_and_content_required")
            blob = self._pack(vector)
            dims = len(vector)
            if dimension is None:
                dimension = dims
            elif dims != dimension:
                raise ValueError("embedding_dimensions_must_match")
            rows.append((actor_id, memory_id, self.model_id, self._digest(content), dims, blob))
        if not rows:
            return 0
        with self._lock, self._connect() as db:
            db.executemany("""INSERT INTO agent_memory_embeddings
                (actor_id,memory_id,model_id,content_sha256,dimensions,vector)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(actor_id,memory_id,model_id) DO UPDATE SET
                    content_sha256=excluded.content_sha256,
                    dimensions=excluded.dimensions,
                    vector=excluded.vector,
                    updated_at=CURRENT_TIMESTAMP""", rows)
        return len(rows)

    def delete_memory(self, *, actor_id: str, memory_id: str) -> bool:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id_required")
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError("memory_id_required")
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "DELETE FROM agent_memory_embeddings WHERE actor_id=? AND memory_id=?",
                (actor_id, memory_id),
            )
            return cursor.rowcount > 0


    def get_backfill_checkpoint(self, *, actor_id: str, agent_id: str) -> dict | None:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id_required")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id_required")
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS memory_embedding_backfill (
                actor_id TEXT NOT NULL, agent_id TEXT NOT NULL, model_id TEXT NOT NULL,
                cursor_created_at TEXT, cursor_memory_id TEXT,
                include_shared INTEGER NOT NULL DEFAULT 1 CHECK(include_shared IN (0,1)),
                completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_id,agent_id,model_id)
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(memory_embedding_backfill)").fetchall()}
            if "include_shared" not in columns:
                db.execute("ALTER TABLE memory_embedding_backfill ADD COLUMN include_shared INTEGER NOT NULL DEFAULT 1 CHECK(include_shared IN (0,1))")
            row = db.execute("""SELECT cursor_created_at,cursor_memory_id,include_shared,completed,updated_at
                FROM memory_embedding_backfill
                WHERE actor_id=? AND agent_id=? AND model_id=?""",
                (actor_id, agent_id, self.model_id)).fetchone()
        if row is None:
            return None
        cursor = None
        if row[0] is not None and row[1] is not None:
            cursor = {"created_at": row[0], "memory_id": row[1]}
        return {"cursor": cursor, "include_shared": bool(row[2]), "completed": bool(row[3]), "updated_at": row[4]}

    def save_backfill_checkpoint(self, *, actor_id: str, agent_id: str,
                                 cursor: dict | None, completed: bool = False,
                                 include_shared: bool = True) -> None:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id_required")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id_required")
        if type(completed) is not bool or type(include_shared) is not bool:
            raise ValueError("boolean_options_required")
        created_at = memory_id = None
        if cursor is not None:
            if not isinstance(cursor, dict):
                raise ValueError("invalid_backfill_cursor")
            created_at, memory_id = cursor.get("created_at"), cursor.get("memory_id")
            if (not isinstance(created_at, str) or len(created_at) > 64
                    or not isinstance(memory_id, str)
                    or not __import__("re").fullmatch(r"[a-f0-9]{32}", memory_id)):
                raise ValueError("invalid_backfill_cursor")
        with self._lock, self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS memory_embedding_backfill (
                actor_id TEXT NOT NULL, agent_id TEXT NOT NULL, model_id TEXT NOT NULL,
                cursor_created_at TEXT, cursor_memory_id TEXT,
                include_shared INTEGER NOT NULL DEFAULT 1 CHECK(include_shared IN (0,1)),
                completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_id,agent_id,model_id)
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(memory_embedding_backfill)").fetchall()}
            if "include_shared" not in columns:
                db.execute("ALTER TABLE memory_embedding_backfill ADD COLUMN include_shared INTEGER NOT NULL DEFAULT 1 CHECK(include_shared IN (0,1))")
            db.execute("""INSERT INTO memory_embedding_backfill
                (actor_id,agent_id,model_id,cursor_created_at,cursor_memory_id,include_shared,completed)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(actor_id,agent_id,model_id) DO UPDATE SET
                cursor_created_at=excluded.cursor_created_at,
                cursor_memory_id=excluded.cursor_memory_id,
                include_shared=excluded.include_shared,
                completed=excluded.completed,updated_at=CURRENT_TIMESTAMP""",
                (actor_id, agent_id, self.model_id, created_at, memory_id, int(include_shared), int(completed)))

    def stats(self, *, actor_id: str) -> dict:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id_required")
        with self._connect() as db:
            row = db.execute("""SELECT COUNT(*), COALESCE(SUM(dimensions),0)
                FROM agent_memory_embeddings WHERE actor_id=? AND model_id=?""",
                (actor_id, self.model_id)).fetchone()
        return {"model_id": self.model_id, "vector_count": row[0],
                "total_dimensions": row[1], "storage": "sqlite_float32"}

class _ClosingConnection:
    """Commit/rollback and always close SQLite handles (required on Windows)."""

    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self._connection

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()
        return False
