"""Typed persistence boundary for specialist-agent plans.

The SQLite adapter is the compatibility implementation. The coordinator is
intentionally not switched to this port until the complete lifecycle has been
moved behind it and the PostgreSQL adapter implements the same contract.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Protocol, Sequence

from automation.agent_plan_contract import agent_plan_request_digest


@dataclass(frozen=True)
class PlanSubtaskRecord:
    subtask_id: str
    agent_id: str
    action: str
    description: str
    required_capabilities: tuple[str, ...]
    required_permissions: tuple[str, ...]
    depends_on: tuple[str, ...]
    requires_approval: bool
    task_id: str | None = None
    status: str = "pending"


@dataclass(frozen=True)
class AgentPlanRecord:
    plan_id: str
    idempotency_key: str
    request_digest: str
    created_at: str
    tenant_id: str | None
    owner_principal_id: str | None
    client_idempotency_key: str
    subtasks: tuple[PlanSubtaskRecord, ...]


class AgentPlanRepository(Protocol):
    """Storage port. Implementations must scope every operation by owner."""

    def create_plan(self, plan: AgentPlanRecord) -> AgentPlanRecord:
        """Atomically create a plan and all subtasks, or return its replay."""
        ...

    def get_plan(
        self, *, plan_id: str, tenant_id: str | None, owner_principal_id: str | None
    ) -> AgentPlanRecord | None:
        """Return an owned plan; foreign and absent plans both return None."""
        ...

    def list_subtasks(
        self, *, plan_id: str, tenant_id: str | None, owner_principal_id: str | None
    ) -> tuple[PlanSubtaskRecord, ...] | None:
        """Return owned subtasks, or None when the plan is not owned/found."""
        ...

    def update_subtask(
        self, *, plan_id: str, subtask_id: str, tenant_id: str | None,
        owner_principal_id: str | None, status: str,
        task_id: str | None = None, expected_statuses: Sequence[str] | None = None,
    ) -> bool:
        """Perform an owner-scoped conditional update; False means no match."""
        ...


class SQLiteAgentPlanRepository:
    """SQLite implementation compatible with the coordinator's existing schema."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_invalid")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=10.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_plans (
                    plan_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    tenant_id TEXT,
                    owner_principal_id TEXT,
                    client_idempotency_key TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_plan_subtasks (
                    plan_id TEXT NOT NULL,
                    subtask_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'emit',
                    description TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    permissions_json TEXT NOT NULL DEFAULT '[]',
                    dependencies_json TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    task_id TEXT,
                    status TEXT NOT NULL,
                    PRIMARY KEY(plan_id, subtask_id),
                    FOREIGN KEY(plan_id) REFERENCES agent_plans(plan_id) ON DELETE CASCADE
                )
            """)
            plan_columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_plans)")}
            for name in ("tenant_id", "owner_principal_id", "client_idempotency_key"):
                if name not in plan_columns:
                    db.execute(f"ALTER TABLE agent_plans ADD COLUMN {name} TEXT")
            db.execute(
                "UPDATE agent_plans SET client_idempotency_key=idempotency_key "
                "WHERE client_idempotency_key IS NULL"
            )
            task_columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_plan_subtasks)")}
            if "permissions_json" not in task_columns:
                db.execute("ALTER TABLE agent_plan_subtasks ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'")
            if "action" not in task_columns:
                db.execute("ALTER TABLE agent_plan_subtasks ADD COLUMN action TEXT NOT NULL DEFAULT 'emit'")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_plans_tenant_owner "
                "ON agent_plans(tenant_id, owner_principal_id, plan_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_plan_subtasks_plan_status "
                "ON agent_plan_subtasks(plan_id, status)"
            )

    @staticmethod
    def _scope(tenant_id: str | None, owner_id: str | None) -> tuple[str | None, str | None]:
        if (tenant_id is None) != (owner_id is None):
            raise ValueError("agent_plan_owner_scope_invalid")
        return tenant_id, owner_id

    @staticmethod
    def _subtask(row: sqlite3.Row) -> PlanSubtaskRecord:
        return PlanSubtaskRecord(
            subtask_id=row["subtask_id"], agent_id=row["agent_id"],
            action=row["action"] if "action" in row.keys() else "emit",
            description=row["description"],
            required_capabilities=tuple(json.loads(row["capabilities_json"])),
            required_permissions=tuple(json.loads(row["permissions_json"])),
            depends_on=tuple(json.loads(row["dependencies_json"])),
            requires_approval=bool(row["requires_approval"]),
            task_id=row["task_id"], status=row["status"],
        )

    @classmethod
    def _record(cls, db: sqlite3.Connection, row: sqlite3.Row) -> AgentPlanRecord:
        subtasks = db.execute(
            "SELECT * FROM agent_plan_subtasks WHERE plan_id=? ORDER BY subtask_id",
            (row["plan_id"],),
        ).fetchall()
        return AgentPlanRecord(
            plan_id=row["plan_id"], idempotency_key=row["idempotency_key"],
            request_digest=row["request_digest"], created_at=row["created_at"],
            tenant_id=row["tenant_id"], owner_principal_id=row["owner_principal_id"],
            client_idempotency_key=row["client_idempotency_key"] or row["idempotency_key"],
            subtasks=tuple(cls._subtask(item) for item in subtasks),
        )

    def create_plan(self, plan: AgentPlanRecord) -> AgentPlanRecord:
        if not isinstance(plan, AgentPlanRecord) or not plan.subtasks:
            raise ValueError("agent_plan_record_invalid")
        self._scope(plan.tenant_id, plan.owner_principal_id)
        if not plan.idempotency_key or not plan.plan_id or not plan.request_digest:
            raise ValueError("agent_plan_record_invalid")
        seen: set[str] = set()
        for item in plan.subtasks:
            if not isinstance(item, PlanSubtaskRecord) or not item.subtask_id or item.subtask_id in seen:
                raise ValueError("agent_plan_subtask_invalid")
            seen.add(item.subtask_id)
            if type(item.requires_approval) is not bool:
                raise ValueError("agent_plan_approval_invalid")
        canonical = [
            {
                "subtask_id": item.subtask_id,
                "agent_id": item.agent_id,
                "action": item.action,
                "description": item.description.strip(),
                "required_capabilities": sorted(item.required_capabilities),
                "required_permissions": sorted(item.required_permissions),
                "depends_on": sorted(item.depends_on),
                "requires_approval": item.requires_approval,
            }
            for item in sorted(plan.subtasks, key=lambda value: value.subtask_id)
        ]
        if plan.tenant_id is not None and plan.owner_principal_id is not None:
            computed_digest = agent_plan_request_digest(
                tenant_id=plan.tenant_id, owner_principal_id=plan.owner_principal_id,
                subtasks=canonical,
            )
        else:
            payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            computed_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if computed_digest != plan.request_digest:
            raise ValueError("agent_plan_request_digest_mismatch")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM agent_plans WHERE idempotency_key=?",
                (plan.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != plan.request_digest:
                    raise ValueError("agent_plan_idempotency_payload_conflict")
                if (existing["tenant_id"], existing["owner_principal_id"]) != (
                    plan.tenant_id, plan.owner_principal_id
                ):
                    raise ValueError("agent_plan_idempotency_scope_conflict")
                return self._record(db, existing)
            db.execute(
                """INSERT INTO agent_plans
                   (plan_id,idempotency_key,request_digest,created_at,tenant_id,
                    owner_principal_id,client_idempotency_key)
                   VALUES (?,?,?,?,?,?,?)""",
                (plan.plan_id, plan.idempotency_key, plan.request_digest, plan.created_at,
                 plan.tenant_id, plan.owner_principal_id, plan.client_idempotency_key),
            )
            for item in plan.subtasks:
                db.execute(
                    """INSERT INTO agent_plan_subtasks
                       (plan_id,subtask_id,agent_id,action,description,capabilities_json,
                        permissions_json,dependencies_json,requires_approval,task_id,status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (plan.plan_id, item.subtask_id, item.agent_id, item.action, item.description,
                     json.dumps(list(item.required_capabilities), separators=(",", ":")),
                     json.dumps(list(item.required_permissions), separators=(",", ":")),
                     json.dumps(list(item.depends_on), separators=(",", ":")),
                     int(item.requires_approval), item.task_id, item.status),
                )
            row = db.execute("SELECT * FROM agent_plans WHERE plan_id=?", (plan.plan_id,)).fetchone()
            return self._record(db, row)

    def get_plan(
        self, *, plan_id: str, tenant_id: str | None, owner_principal_id: str | None
    ) -> AgentPlanRecord | None:
        self._scope(tenant_id, owner_principal_id)
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM agent_plans WHERE plan_id=?
                   AND tenant_id IS ? AND owner_principal_id IS ?""",
                (plan_id, tenant_id, owner_principal_id),
            ).fetchone()
            return None if row is None else self._record(db, row)

    def list_subtasks(
        self, *, plan_id: str, tenant_id: str | None, owner_principal_id: str | None
    ) -> tuple[PlanSubtaskRecord, ...] | None:
        plan = self.get_plan(
            plan_id=plan_id, tenant_id=tenant_id, owner_principal_id=owner_principal_id
        )
        return None if plan is None else plan.subtasks

    def update_subtask(
        self, *, plan_id: str, subtask_id: str, tenant_id: str | None,
        owner_principal_id: str | None, status: str,
        task_id: str | None = None, expected_statuses: Sequence[str] | None = None,
    ) -> bool:
        self._scope(tenant_id, owner_principal_id)
        if not isinstance(status, str) or not status.strip():
            raise ValueError("agent_plan_subtask_status_invalid")
        with self._connect() as db:
            predicates = [
                "plan_id=?", "subtask_id=?",
                "EXISTS (SELECT 1 FROM agent_plans p WHERE p.plan_id=agent_plan_subtasks.plan_id "
                "AND p.tenant_id IS ? AND p.owner_principal_id IS ?)",
            ]
            params: list[object] = [plan_id, subtask_id, tenant_id, owner_principal_id]
            if expected_statuses is not None:
                values = tuple(expected_statuses)
                if not values:
                    return False
                predicates.append("status IN (" + ",".join("?" for _ in values) + ")")
                params.extend(values)
            if task_id is None:
                cursor = db.execute(
                    "UPDATE agent_plan_subtasks SET status=? WHERE " + " AND ".join(predicates),
                    [status, *params],
                )
            else:
                cursor = db.execute(
                    "UPDATE agent_plan_subtasks SET task_id=?,status=? WHERE " + " AND ".join(predicates),
                    [task_id, status, *params],
                )
            return cursor.rowcount == 1
