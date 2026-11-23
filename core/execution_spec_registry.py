"""Owner-scoped, immutable registry for versioned agent execution specs."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Callable

from automation.agent_execution_spec import AgentExecutionSpec


class ExecutionSpecRegistry:
    """Persist immutable spec versions; authorization is supplied by the host."""

    def __init__(self, database: str | Path, *, authorize: Callable[[str, str, str], bool]):
        if not callable(authorize):
            raise TypeError("authorize must be a host-provided callable.")
        self.database = str(database)
        if self.database == ":memory:":
            raise ValueError("a file-backed database is required for durable execution specs.")
        self.authorize = authorize
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS execution_specs (
                owner_id TEXT NOT NULL, spec_id TEXT NOT NULL, version INTEGER NOT NULL,
                canonical_json TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(owner_id, spec_id, version))""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS execution_specs_no_update
                BEFORE UPDATE ON execution_specs BEGIN SELECT RAISE(ABORT, "immutable execution spec"); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS execution_specs_no_delete
                BEFORE DELETE ON execution_specs BEGIN SELECT RAISE(ABORT, "immutable execution spec"); END""")

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _identity(value: str, field: str) -> str:
        if type(value) is not str or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
            raise ValueError(f"{field} must be bounded non-empty text.")
        return value.strip()

    def _check(self, actor_id: str, owner_id: str, action: str, spec_id: str) -> None:
        actor = self._identity(actor_id, "actor_id")
        owner = self._identity(owner_id, "owner_id")
        if self.authorize(actor, owner, action) is not True:
            raise PermissionError("execution spec access denied.")
        self._identity(spec_id, "spec_id")

    def put(self, *, actor_id: str, owner_id: str, spec: AgentExecutionSpec) -> str:
        if not isinstance(spec, AgentExecutionSpec):
            raise TypeError("spec must be an AgentExecutionSpec.")
        actor_id = self._identity(actor_id, "actor_id")
        owner_id = self._identity(owner_id, "owner_id")
        self._check(actor_id, owner_id, "execution_spec:write", spec.spec_id)
        payload = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        from datetime import datetime, timezone
        created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT MAX(version) AS version FROM execution_specs WHERE owner_id=? AND spec_id=?", (owner_id, spec.spec_id)).fetchone()
            latest = row["version"] or 0
            if spec.version != latest + 1:
                raise ValueError("spec version must be the next immutable version.")
            db.execute("INSERT INTO execution_specs VALUES(?,?,?,?,?,?)", (owner_id, spec.spec_id, spec.version, payload, digest, created))
        return digest

    def get(self, *, actor_id: str, owner_id: str, spec_id: str, version: int | None = None) -> AgentExecutionSpec | None:
        actor_id = self._identity(actor_id, "actor_id")
        owner_id = self._identity(owner_id, "owner_id")
        self._check(actor_id, owner_id, "execution_spec:read", spec_id)
        if version is not None and (type(version) is not int or version < 1):
            raise ValueError("version must be a positive integer.")
        with self._connect() as db:
            if version is None:
                row = db.execute("SELECT canonical_json, sha256 FROM execution_specs WHERE owner_id=? AND spec_id=? ORDER BY version DESC LIMIT 1", (owner_id, spec_id)).fetchone()
            else:
                row = db.execute("SELECT canonical_json, sha256 FROM execution_specs WHERE owner_id=? AND spec_id=? AND version=?", (owner_id, spec_id, version)).fetchone()
        if row is None:
            return None
        payload = row["canonical_json"]
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["sha256"]:
            raise RuntimeError("stored execution spec integrity check failed.")
        return AgentExecutionSpec.from_dict(json.loads(payload))

    def list_ids(self, *, actor_id: str, owner_id: str, limit: int = 100) -> tuple[str, ...]:
        actor_id = self._identity(actor_id, "actor_id")
        owner_id = self._identity(owner_id, "owner_id")
        self._check(actor_id, owner_id, "execution_spec:read", "registry")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        with self._connect() as db:
            rows = db.execute("SELECT spec_id FROM execution_specs WHERE owner_id=? GROUP BY spec_id ORDER BY spec_id LIMIT ?", (owner_id, limit)).fetchall()
        return tuple(row["spec_id"] for row in rows)

