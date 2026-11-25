"""Persistence ports and SQLite adapter for specialist-agent manifests.

The registry stores routing metadata only. It never loads or executes agent code.
Adapters must return validated manifest values and make mutations atomic.
"""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from threading import RLock
from typing import Any, Callable, Iterable, Protocol


class SpecialistAgentRegistry(Protocol):
    """Authoritative source of specialist routing manifests."""

    def initialize(self, agents: Iterable[Any]) -> None: ...
    def list_agents(self) -> tuple[Any, ...]: ...
    def register_agent(self, agent: Any) -> None: ...
    def set_agent_enabled(self, agent_id: str, enabled: bool) -> Any: ...


class SQLiteSpecialistAgentRegistry:
    """Local/single-host registry adapter; not a distributed authority."""

    def __init__(
        self,
        database_path: str,
        *,
        agent_factory: Callable[..., Any],
        validate_agent: Callable[[Any], None],
    ) -> None:
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(agent_factory) or not callable(validate_agent):
            raise TypeError("agent_factory and validate_agent must be callable.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._agent_factory = agent_factory
        self._validate_agent = validate_agent
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS specialist_agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    action TEXT NOT NULL,
                    permissions_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def initialize(self, agents: Iterable[Any]) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            for agent in agents:
                self._validate_agent(agent)
                db.execute("""
                    INSERT OR IGNORE INTO specialist_agents(
                        agent_id, name, capabilities_json, action, permissions_json,
                        enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    agent.agent_id, agent.name,
                    json.dumps(sorted(agent.capabilities)), agent.action,
                    json.dumps(sorted(agent.permissions)), int(agent.enabled), now, now,
                ))

    def _from_row(self, row: sqlite3.Row) -> Any:
        try:
            capabilities = json.loads(row["capabilities_json"])
            permissions = json.loads(row["permissions_json"])
            if not isinstance(capabilities, list) or not isinstance(permissions, list):
                raise ValueError("registry collections must be JSON arrays")
            if any(not isinstance(value, str) for value in capabilities + permissions):
                raise ValueError("registry values must be strings")
            if (
                len(capabilities) > 32 or len(permissions) > 32
                or len(set(capabilities)) != len(capabilities)
                or len(set(permissions)) != len(permissions)
                or row["enabled"] not in (0, 1)
            ):
                raise ValueError("registry manifest is invalid")
            agent = self._agent_factory(
                agent_id=row["agent_id"], name=row["name"],
                capabilities=frozenset(capabilities), action=row["action"],
                permissions=frozenset(permissions), enabled=bool(row["enabled"]),
            )
            self._validate_agent(agent)
            return agent
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("persisted specialist registry contains an invalid manifest.") from exc

    def list_agents(self) -> tuple[Any, ...]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM specialist_agents ORDER BY agent_id").fetchall()
        return tuple(self._from_row(row) for row in rows)

    def register_agent(self, agent: Any) -> None:
        from datetime import datetime, timezone
        self._validate_agent(agent)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            try:
                db.execute("""
                    INSERT INTO specialist_agents(
                        agent_id, name, capabilities_json, action, permissions_json,
                        enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    agent.agent_id, agent.name, json.dumps(sorted(agent.capabilities)),
                    agent.action, json.dumps(sorted(agent.permissions)), int(agent.enabled),
                    now, now,
                ))
            except sqlite3.IntegrityError as exc:
                raise ValueError("agent ID is already registered.") from exc

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> Any:
        from datetime import datetime, timezone
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM specialist_agents WHERE agent_id=?", (agent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(agent_id)
            current = self._from_row(row)
            updated = self._agent_factory(
                agent_id=current.agent_id, name=current.name,
                capabilities=current.capabilities, action=current.action,
                permissions=current.permissions, enabled=enabled,
            )
            self._validate_agent(updated)
            db.execute(
                "UPDATE specialist_agents SET enabled=?,updated_at=? WHERE agent_id=?",
                (int(enabled), now, agent_id),
            )
            return updated
