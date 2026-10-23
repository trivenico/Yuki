"""Bounded, durable specialist-agent delegation over Yuki's task runtime.

The coordinator plans and dispatches allow-listed task definitions. It does not
execute model-generated code, invoke tools directly, or bypass the workflow
engine's approval policy. Workers remain responsible for execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterable

from automation.task_runtime import TaskRecord, TaskRuntime
from automation.workflow_store import WorkflowConflict

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_PERMISSION = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")
_MAX_SUBTASKS = 20
_MAX_PLAN_BYTES = 24 * 1024


class AgentPlanError(ValueError):
    """A plan is malformed, unsafe, cyclic, or cannot be routed."""


@dataclass(frozen=True)
class SpecialistAgent:
    agent_id: str
    name: str
    capabilities: frozenset[str]
    action: str
    permissions: frozenset[str] = frozenset()
    enabled: bool = True


@dataclass(frozen=True)
class SubtaskSpec:
    subtask_id: str
    description: str
    required_capabilities: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    requires_approval: bool = True


@dataclass(frozen=True)
class PlannedSubtask:
    subtask_id: str
    agent_id: str
    description: str
    required_capabilities: tuple[str, ...]
    depends_on: tuple[str, ...]
    requires_approval: bool
    task_id: str | None
    status: str


@dataclass(frozen=True)
class AgentPlan:
    plan_id: str
    idempotency_key: str
    created_at: str
    subtasks: tuple[PlannedSubtask, ...]


class SpecialistAgentCoordinator:
    """Deterministic capability routing and dependency-gated task dispatch."""

    def __init__(
        self,
        runtime: TaskRuntime,
        database_path: str,
        agents: Iterable[SpecialistAgent],
        *,
        authorize: Callable[[str, str, str], bool],
        max_subtasks: int = _MAX_SUBTASKS,
    ):
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("runtime must be a TaskRuntime.")
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not callable(authorize):
            raise TypeError("a trusted authorization callback is required.")
        if type(max_subtasks) is not int or not 1 <= max_subtasks <= _MAX_SUBTASKS:
            raise ValueError("max_subtasks must be between 1 and 20.")
        self.runtime = runtime
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        self.max_subtasks = max_subtasks
        agent_map: dict[str, SpecialistAgent] = {}
        for agent in agents:
            self._validate_agent(agent)
            if agent.agent_id in agent_map:
                raise ValueError("agent IDs must be unique.")
            agent_map[agent.agent_id] = agent
        self.agents = agent_map
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_plans (
                    plan_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_plan_subtasks (
                    plan_id TEXT NOT NULL,
                    subtask_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    permissions_json TEXT NOT NULL DEFAULT "[]",
                    dependencies_json TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    task_id TEXT,
                    status TEXT NOT NULL,
                    PRIMARY KEY(plan_id, subtask_id),
                    FOREIGN KEY(plan_id) REFERENCES agent_plans(plan_id) ON DELETE CASCADE
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_plan_subtasks)")}
            if "permissions_json" not in columns:
                db.execute('ALTER TABLE agent_plan_subtasks ADD COLUMN permissions_json TEXT NOT NULL DEFAULT "[]"')
            db.execute("CREATE INDEX IF NOT EXISTS idx_agent_plan_subtasks_plan_status ON agent_plan_subtasks(plan_id, status)")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    @staticmethod
    def _validate_agent(agent: SpecialistAgent) -> None:
        if not isinstance(agent, SpecialistAgent):
            raise TypeError("agents must be SpecialistAgent values.")
        if not isinstance(agent.agent_id, str) or not _ID.fullmatch(agent.agent_id):
            raise ValueError("agent_id must be a bounded identifier.")
        if not isinstance(agent.name, str) or not agent.name.strip() or len(agent.name) > 100:
            raise ValueError("agent name must be non-empty bounded text.")
        if not isinstance(agent.action, str) or not _ID.fullmatch(agent.action):
            raise ValueError("agent action must be an allow-listed action identifier.")
        if not isinstance(agent.capabilities, frozenset) or any(
            not isinstance(item, str) or not _ID.fullmatch(item) for item in agent.capabilities
        ):
            raise ValueError("capabilities must be a frozenset of identifiers.")
        if not isinstance(agent.permissions, frozenset) or any(
            not isinstance(item, str) or not _PERMISSION.fullmatch(item) for item in agent.permissions
        ):
            raise ValueError("permissions must be a frozenset of permission identifiers.")
        if type(agent.enabled) is not bool:
            raise ValueError("enabled must be a boolean.")

    def _allowed(self, actor_id: str, action: str, resource_id: str) -> bool:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            return False
        try:
            return self.authorize(actor_id.strip(), action, resource_id) is True
        except Exception:
            return False

    @staticmethod
    def _validate_subtasks(subtasks: Iterable[SubtaskSpec], max_subtasks: int) -> tuple[SubtaskSpec, ...]:
        if isinstance(subtasks, (str, bytes)):
            raise AgentPlanError("subtasks must be a sequence of subtask specifications.")
        try:
            items = tuple(subtasks)
        except TypeError as exc:
            raise AgentPlanError("subtasks must be iterable.") from exc
        if not 1 <= len(items) <= max_subtasks:
            raise AgentPlanError(f"plan must contain 1-{max_subtasks} subtasks.")
        ids: set[str] = set()
        for item in items:
            if not isinstance(item, SubtaskSpec):
                raise AgentPlanError("every item must be a SubtaskSpec.")
            if not isinstance(item.subtask_id, str) or not _ID.fullmatch(item.subtask_id):
                raise AgentPlanError("subtask IDs must be bounded identifiers.")
            if item.subtask_id in ids:
                raise AgentPlanError("subtask IDs must be unique.")
            ids.add(item.subtask_id)
            if not isinstance(item.description, str) or not item.description.strip() or len(item.description) > 2000 or "\x00" in item.description:
                raise AgentPlanError("subtask descriptions must be non-empty bounded text.")
            if not isinstance(item.required_capabilities, tuple) or not item.required_capabilities:
                raise AgentPlanError("each subtask must declare required capabilities.")
            if any(not isinstance(c, str) or not _ID.fullmatch(c) for c in item.required_capabilities):
                raise AgentPlanError("required capabilities must be bounded identifiers.")
            if len(set(item.required_capabilities)) != len(item.required_capabilities):
                raise AgentPlanError("required capabilities must be unique.")
            if not isinstance(item.depends_on, tuple) or any(not isinstance(d, str) or not _ID.fullmatch(d) for d in item.depends_on):
                raise AgentPlanError("dependencies must be bounded subtask identifiers.")
            if item.subtask_id in item.depends_on or len(set(item.depends_on)) != len(item.depends_on):
                raise AgentPlanError("self-dependencies and duplicate dependencies are not allowed.")
            if type(item.requires_approval) is not bool:
                raise AgentPlanError("requires_approval must be a boolean.")
        for item in items:
            if any(dep not in ids for dep in item.depends_on):
                raise AgentPlanError(f"subtask {item.subtask_id} references an unknown dependency.")
        by_id = {item.subtask_id: item for item in items}
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                raise AgentPlanError("dependency graph contains a cycle.")
            if node in visited:
                return
            visiting.add(node)
            for dep in by_id[node].depends_on:
                visit(dep)
            visiting.remove(node)
            visited.add(node)
        for item in items:
            visit(item.subtask_id)
        canonical = [
            {"subtask_id": i.subtask_id, "description": i.description.strip(),
             "required_capabilities": sorted(i.required_capabilities),
             "required_permissions": sorted(i.required_permissions),
             "depends_on": sorted(i.depends_on), "requires_approval": i.requires_approval}
            for i in sorted(items, key=lambda x: x.subtask_id)
        ]
        if len(json.dumps(canonical, ensure_ascii=False).encode("utf-8")) > _MAX_PLAN_BYTES:
            raise AgentPlanError("plan definition exceeds the byte limit.")
        return items

    def _route(self, subtask: SubtaskSpec) -> SpecialistAgent:
        needed = set(subtask.required_capabilities)
        candidates = [
            agent for agent in self.agents.values()
            if agent.enabled and needed <= agent.capabilities
        ]
        if not candidates:
            raise AgentPlanError(f"no enabled specialist supports subtask {subtask.subtask_id}.")
        # Deterministic least-overprovisioned match, then stable ID tie-break.
        return sorted(candidates, key=lambda a: (len(a.capabilities - needed), a.agent_id))[0]

    def submit(self, *, actor_id: str, idempotency_key: str, subtasks: Iterable[SubtaskSpec]) -> AgentPlan:
        if not self._allowed(actor_id, "agent:plan", ""):
            raise PermissionError("agent planning is not authorized.")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200 or "\x00" in idempotency_key:
            raise ValueError("idempotency_key must be non-empty bounded text.")
        items = self._validate_subtasks(subtasks, self.max_subtasks)
        assignments = {item.subtask_id: self._route(item) for item in items}
        canonical = [
            {"subtask_id": i.subtask_id, "description": i.description.strip(),
             "required_capabilities": sorted(i.required_capabilities),
             "required_permissions": sorted(i.required_permissions),
             "depends_on": sorted(i.depends_on), "requires_approval": i.requires_approval,
             "agent_id": assignments[i.subtask_id].agent_id,
             "action": assignments[i.subtask_id].action}
            for i in sorted(items, key=lambda x: x.subtask_id)
        ]
        payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        key = idempotency_key.strip()
        plan_id = hashlib.sha256(("yuki-agent-plan\0" + key).encode("utf-8")).hexdigest()[:32]
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM agent_plans WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise WorkflowConflict("idempotency key was already used for a different agent plan.")
                return self._read_plan(db, existing["plan_id"])
            db.execute("INSERT INTO agent_plans(plan_id, idempotency_key, request_digest, created_at) VALUES (?, ?, ?, ?)",
                       (plan_id, key, digest, now))
            for item in items:
                agent = assignments[item.subtask_id]
                db.execute("""
                    INSERT INTO agent_plan_subtasks(
                        plan_id, subtask_id, agent_id, description, capabilities_json, permissions_json,
                        dependencies_json, requires_approval, task_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending')
                """, (plan_id, item.subtask_id, agent.agent_id, item.description.strip(),
                      json.dumps(sorted(item.required_capabilities)),
                      json.dumps(sorted(item.required_permissions)),
                      json.dumps(list(item.depends_on)), int(item.requires_approval)))
            return self._read_plan(db, plan_id)

    def advance(self, *, actor_id: str, plan_id: str) -> AgentPlan:
        """Create only dependency-ready tasks; workers still execute them separately."""
        if not self._allowed(actor_id, "agent:dispatch", plan_id):
            raise PermissionError("agent dispatch is not authorized.")
        # Never hold a coordinator SQLite write lock while calling TaskRuntime:
        # both may share one database file. Stable idempotency keys make concurrent
        # advances and recovery after a partial write safe.
        with self._connect() as db:
            plan = db.execute("SELECT * FROM agent_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if plan is None:
                raise KeyError("agent plan not found.")
            plan_key = plan["idempotency_key"]
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM agent_plan_subtasks WHERE plan_id=? ORDER BY subtask_id", (plan_id,)
            ).fetchall()]
        by_id = {row["subtask_id"]: row for row in rows}

        for row in rows:
            if row["task_id"] is not None or row["status"] in {"succeeded", "failed", "cancelled", "blocked"}:
                continue
            dependencies = json.loads(row["dependencies_json"])
            dependency_states = []
            waiting = False
            blocked = False
            for dep_id in dependencies:
                dep = by_id[dep_id]
                if dep["status"] in {"failed", "cancelled", "blocked"}:
                    blocked = True
                    break
                if dep["task_id"] is None:
                    waiting = True
                    break
                dep_task = self.runtime.get(actor_id=actor_id, task_id=dep["task_id"])
                if dep_task.state in {"failed", "cancelled", "interrupted"}:
                    blocked = True
                    break
                if dep_task.state != "succeeded":
                    waiting = True
                    break
                dependency_states.append(dep_task.state)
            if blocked:
                self._set_subtask(plan_id, row["subtask_id"], status="blocked")
                row["status"] = "blocked"
                continue
            if waiting:
                continue
            agent = self.agents.get(row["agent_id"])
            if agent is None or not agent.enabled:
                self._set_subtask(plan_id, row["subtask_id"], status="failed")
                row["status"] = "failed"
                continue
            task = self.runtime.enqueue(
                actor_id=actor_id,
                idempotency_key=f"agent-plan:{plan_key}:{row['subtask_id']}",
                name=f"agent-{agent.agent_id}-{row['subtask_id']}",
                steps=[{
                    "name": row["subtask_id"], "action": agent.action,
                    "argument": row["description"],
                    "requires_approval": bool(row["requires_approval"]),
                }],
            )
            self._set_subtask(plan_id, row["subtask_id"], task_id=task.task_id, status=task.state)
            row["task_id"] = task.task_id
            row["status"] = task.state

        # Refresh runtime states outside coordinator DB transactions.
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM agent_plan_subtasks WHERE plan_id=? ORDER BY subtask_id", (plan_id,)
            ).fetchall()]
        for row in rows:
            if row["task_id"] is None or row["status"] in {"blocked", "failed", "cancelled"}:
                continue
            task = self.runtime.get(actor_id=actor_id, task_id=row["task_id"])
            if task.state == "succeeded":
                state = "succeeded"
            elif task.state in {"failed", "cancelled", "interrupted"}:
                state = "cancelled" if task.state == "cancelled" else "failed"
            else:
                state = task.state
            if state != row["status"]:
                self._set_subtask(plan_id, row["subtask_id"], status=state)
        return self.get_plan(actor_id=actor_id, plan_id=plan_id)

    def _set_subtask(self, plan_id: str, subtask_id: str, *, status: str, task_id: str | None = None) -> None:
        with self._connect() as db:
            if task_id is None:
                db.execute(
                    "UPDATE agent_plan_subtasks SET status=? WHERE plan_id=? AND subtask_id=?",
                    (status, plan_id, subtask_id),
                )
            else:
                db.execute(
                    "UPDATE agent_plan_subtasks SET task_id=?, status=? WHERE plan_id=? AND subtask_id=?",
                    (task_id, status, plan_id, subtask_id),
                )

    def get_plan(self, *, actor_id: str, plan_id: str) -> AgentPlan:
        if not self._allowed(actor_id, "agent:read", plan_id):
            raise PermissionError("agent plan read is not authorized.")
        with self._connect() as db:
            row = db.execute("SELECT plan_id FROM agent_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if row is None:
                raise KeyError("agent plan not found.")
            return self._read_plan(db, plan_id)

    @staticmethod
    def _read_plan(db: sqlite3.Connection, plan_id: str) -> AgentPlan:
        row = db.execute("SELECT * FROM agent_plans WHERE plan_id=?", (plan_id,)).fetchone()
        subtasks = db.execute("SELECT * FROM agent_plan_subtasks WHERE plan_id=? ORDER BY subtask_id", (plan_id,)).fetchall()
        return AgentPlan(
            plan_id=row["plan_id"], idempotency_key=row["idempotency_key"], created_at=row["created_at"],
            subtasks=tuple(PlannedSubtask(
                subtask_id=s["subtask_id"], agent_id=s["agent_id"], description=s["description"],
                required_capabilities=tuple(json.loads(s["capabilities_json"])),
                depends_on=tuple(json.loads(s["dependencies_json"])),
                requires_approval=bool(s["requires_approval"]), task_id=s["task_id"], status=s["status"],
            ) for s in subtasks),
        )


class _Connection:
    """Small context wrapper that commits or rolls back and always closes SQLite."""
    def __init__(self, db: sqlite3.Connection):
        self.db = db
    def __enter__(self):
        return self.db
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            self.db.close()
        return False
