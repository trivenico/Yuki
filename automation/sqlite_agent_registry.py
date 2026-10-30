"""SQLite-backed persistence adapter for Yuki specialist-agent contracts.

The adapter stores declarative agent metadata only. It does not store secrets,
execute handlers, or grant permissions outside the contract's authorization checks.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from threading import RLock

from core.agent_contracts import AgentSpec, DelegationRequest


class SQLiteAgentRegistry:
    """Durable registry using the same SQLite database as WorkflowRunStore."""

    def __init__(self, database_path: str, *, max_agents: int | None = None) -> None:
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if max_agents is not None and (type(max_agents) is not int or max_agents < 1):
            raise ValueError("max_agents must be None (unlimited) or a positive integer.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.max_agents = max_agents
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS specialist_agents (
                    agent_id TEXT PRIMARY KEY,
                    spec_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
    
    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _serialize(spec: AgentSpec) -> str:
        data = asdict(spec)
        data["capabilities"] = sorted(spec.capabilities)
        data["allowed_tools"] = sorted(spec.allowed_tools)
        data["delegation_targets"] = sorted(spec.delegation_targets)
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _deserialize(raw: str) -> AgentSpec:
        data = json.loads(raw)
        data["capabilities"] = frozenset(data["capabilities"])
        data["allowed_tools"] = frozenset(data["allowed_tools"])
        data["delegation_targets"] = frozenset(data["delegation_targets"])
        data["provider_preferences"] = tuple(data["provider_preferences"])
        return AgentSpec(**data)

    def register(self, spec: AgentSpec) -> AgentSpec:
        if not isinstance(spec, AgentSpec):
            raise TypeError("spec must be an AgentSpec.")
        encoded = self._serialize(spec)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) AS total FROM specialist_agents").fetchone()["total"]
            if self.max_agents is not None and count >= self.max_agents:
                raise ValueError("configured agent registry capacity reached.")
            if db.execute("SELECT 1 FROM specialist_agents WHERE agent_id=?", (spec.agent_id,)).fetchone():
                raise ValueError("agent is already registered.")
            unknown = set(spec.delegation_targets)
            if unknown:
                placeholders = ",".join("?" for _ in unknown)
                rows = db.execute(
                    f"SELECT agent_id FROM specialist_agents WHERE agent_id IN ({placeholders})",
                    tuple(sorted(unknown)),
                ).fetchall()
                registered = {row["agent_id"] for row in rows}
                if registered != unknown:
                    raise ValueError("delegation targets must be registered before the delegating agent.")
            db.execute(
                "INSERT INTO specialist_agents(agent_id, spec_json) VALUES (?, ?)",
                (spec.agent_id, encoded),
            )
        return spec

    def get(self, agent_id: str) -> AgentSpec | None:
        if not isinstance(agent_id, str):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT spec_json FROM specialist_agents WHERE agent_id=?", (agent_id,)
            ).fetchone()
        return self._deserialize(row["spec_json"]) if row else None

    def list_agents(self) -> tuple[AgentSpec, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT spec_json FROM specialist_agents ORDER BY agent_id"
            ).fetchall()
        return tuple(self._deserialize(row["spec_json"]) for row in rows)

    def authorize_delegation(self, request: DelegationRequest) -> AgentSpec:
        if not isinstance(request, DelegationRequest):
            raise TypeError("request must be a DelegationRequest.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT agent_id, spec_json FROM specialist_agents WHERE agent_id IN (?, ?)",
                (request.parent_agent_id, request.target_agent_id),
            ).fetchall()
        specs = {row["agent_id"]: self._deserialize(row["spec_json"]) for row in rows}
        source = specs.get(request.parent_agent_id)
        target = specs.get(request.target_agent_id)
        if source is None or target is None:
            raise PermissionError("delegation references an unknown agent.")
        if "agent:delegate" not in source.capabilities:
            raise PermissionError("source agent cannot delegate.")
        if request.target_agent_id not in source.delegation_targets:
            raise PermissionError("target agent is not in the source delegation allow-list.")
        if not request.required_capabilities.issubset(target.capabilities):
            raise PermissionError("target agent lacks required capabilities.")
        return target
