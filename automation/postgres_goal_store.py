"""Tenant-scoped PostgreSQL repository for distributed workflow goals.

Workflow runs remain the execution records. This repository only groups those
runs, records dependency edges, and derives a goal outcome from terminal runs.
Every public operation requires the authenticated tenant and owner scope.
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from core.task_lifecycle import aggregate_goal_outcome


class PostgresGoalConflict(RuntimeError):
    """A goal or dependency operation conflicts with current persisted state."""


class PostgresGoalNotFound(KeyError):
    """The requested goal is not visible in the supplied tenant/owner scope."""


class PostgresWorkflowGoalStore:
    """PostgreSQL persistence adapter for goal membership and dependency metadata.

    This adapter deliberately does not create, execute, cancel, or mutate
    workflow_runs. Those operations belong to WorkflowRepository/TaskRuntime.
    """

    def __init__(
        self, database_url: str, *, min_pool_size: int = 1,
        max_pool_size: int = 10, pool_timeout: float = 10.0,
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("goal_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("goal_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("goal_pool_timeout_invalid")
        self.pool = ConnectionPool(
            conninfo=database_url, min_size=min_pool_size, max_size=max_pool_size,
            timeout=float(pool_timeout), kwargs={"row_factory": dict_row}, open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
            with self.pool.connection() as conn:
                row = conn.execute(
                    "SELECT to_regclass('public.workflow_goals') AS table_name"
                ).fetchone()
                if row is None or row["table_name"] is None:
                    raise RuntimeError("workflow_goal_schema_not_migrated")
        except Exception:
            self.pool.close()
            raise

    def close(self) -> None:
        self.pool.close()

    @staticmethod
    def _scope(tenant_id: str, owner_principal_id: str) -> tuple[str, str]:
        for value, field, maximum in (
            (tenant_id, "tenant_id", 200),
            (owner_principal_id, "owner_principal_id", 200),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
                raise ValueError(f"{field}_invalid")
        return tenant_id, owner_principal_id

    @staticmethod
    def _goal_id(goal_id: str) -> str:
        if not isinstance(goal_id, str) or not goal_id.strip() or len(goal_id) > 256 or "\x00" in goal_id:
            raise ValueError("goal_id_invalid")
        return goal_id

    @staticmethod
    def _task_id(task_id: str) -> str:
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 256 or "\x00" in task_id:
            raise ValueError("task_id_invalid")
        return task_id

    @staticmethod
    def _event(conn, tenant_id: str, goal_id: str, owner_id: str,
               event_type: str, detail: dict[str, Any], task_id: str | None = None) -> None:
        if not isinstance(event_type, str) or not 1 <= len(event_type) <= 96 or "\x00" in event_type:
            raise ValueError("event_type_invalid")
        if not isinstance(detail, dict):
            raise ValueError("event_detail_must_be_object")
        conn.execute(
            """INSERT INTO workflow_goal_events
               (tenant_id, goal_id, owner_principal_id, task_id, event_type, detail_json)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (tenant_id, goal_id, owner_id, task_id, event_type, Jsonb(detail)),
        )

    @staticmethod
    def _require_goal(conn, tenant_id: str, goal_id: str, owner_id: str, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        row = conn.execute(
            """SELECT tenant_id, goal_id, owner_principal_id, state, outcome,
                      version, created_at, updated_at
               FROM workflow_goals
               WHERE tenant_id=%s AND goal_id=%s AND owner_principal_id=%s""" + suffix,
            (tenant_id, goal_id, owner_id),
        ).fetchone()
        if row is None:
            raise PostgresGoalNotFound("goal_not_found")
        return row

    def create_goal(self, *, tenant_id: str, owner_principal_id: str,
                    goal_id: str) -> dict[str, Any]:
        tenant_id, owner_principal_id = self._scope(tenant_id, owner_principal_id)
        goal_id = self._goal_id(goal_id)
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """INSERT INTO workflow_goals
                       (tenant_id, goal_id, owner_principal_id, state, outcome)
                       VALUES (%s,%s,%s,'active',NULL)
                       ON CONFLICT (tenant_id, goal_id) DO NOTHING
                       RETURNING tenant_id, goal_id, owner_principal_id, state,
                                 outcome, version, created_at, updated_at""",
                    (tenant_id, goal_id, owner_principal_id),
                ).fetchone()
                if row is None:
                    existing = self._require_goal(conn, tenant_id, goal_id, owner_principal_id)
                    row = existing
                else:
                    self._event(conn, tenant_id, goal_id, owner_principal_id, "goal.created", {})
                return dict(row)

    def attach_task(self, *, tenant_id: str, owner_principal_id: str,
                    goal_id: str, task_id: str) -> bool:
        tenant_id, owner_principal_id = self._scope(tenant_id, owner_principal_id)
        goal_id, task_id = self._goal_id(goal_id), self._task_id(task_id)
        with self.pool.connection() as conn:
            with conn.transaction():
                goal = self._require_goal(conn, tenant_id, goal_id, owner_principal_id, lock=True)
                if goal["state"] != "active":
                    raise PostgresGoalConflict("goal_not_active")
                existing = conn.execute(
                    """SELECT goal_id FROM workflow_goal_tasks
                       WHERE tenant_id=%s AND task_id=%s""",
                    (tenant_id, task_id),
                ).fetchone()
                if existing is not None:
                    if existing["goal_id"] != goal_id:
                        raise PostgresGoalConflict("task_already_belongs_to_another_goal")
                    return False
                conn.execute(
                    """INSERT INTO workflow_goal_tasks
                       (tenant_id, goal_id, owner_principal_id, task_id)
                       VALUES (%s,%s,%s,%s)""",
                    (tenant_id, goal_id, owner_principal_id, task_id),
                )
                self._event(conn, tenant_id, goal_id, owner_principal_id,
                            "task.attached", {}, task_id)
                conn.execute(
                    """UPDATE workflow_goals SET updated_at=clock_timestamp(), version=version+1
                       WHERE tenant_id=%s AND goal_id=%s AND owner_principal_id=%s""",
                    (tenant_id, goal_id, owner_principal_id),
                )
                return True

    def add_dependency(self, *, tenant_id: str, owner_principal_id: str,
                       goal_id: str, task_id: str, depends_on_task_id: str) -> bool:
        tenant_id, owner_principal_id = self._scope(tenant_id, owner_principal_id)
        goal_id = self._goal_id(goal_id)
        task_id, depends_on_task_id = self._task_id(task_id), self._task_id(depends_on_task_id)
        if task_id == depends_on_task_id:
            raise ValueError("self_dependency_not_allowed")
        with self.pool.connection() as conn:
            with conn.transaction():
                self._require_goal(conn, tenant_id, goal_id, owner_principal_id, lock=True)
                # The goal-row lock serializes graph edits, making the recursive
                # cycle check and edge insert one atomic operation.
                cycle = conn.execute(
                    """WITH RECURSIVE reachable(task_id) AS (
                           SELECT depends_on_task_id
                           FROM workflow_goal_dependencies
                           WHERE tenant_id=%s AND goal_id=%s AND task_id=%s
                         UNION
                           SELECT d.depends_on_task_id
                           FROM workflow_goal_dependencies d
                           JOIN reachable r ON d.task_id=r.task_id
                           WHERE d.tenant_id=%s AND d.goal_id=%s
                       )
                       SELECT 1 FROM reachable WHERE task_id=%s LIMIT 1""",
                    (tenant_id, goal_id, depends_on_task_id,
                     tenant_id, goal_id, task_id),
                ).fetchone()
                if cycle is not None:
                    raise PostgresGoalConflict("dependency_cycle")
                inserted = conn.execute(
                    """INSERT INTO workflow_goal_dependencies
                       (tenant_id, goal_id, task_id, depends_on_task_id)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING RETURNING task_id""",
                    (tenant_id, goal_id, task_id, depends_on_task_id),
                ).fetchone()
                if inserted is None:
                    return False
                self._event(conn, tenant_id, goal_id, owner_principal_id,
                            "dependency.added", {"depends_on_task_id": depends_on_task_id}, task_id)
                conn.execute(
                    """UPDATE workflow_goals SET updated_at=clock_timestamp(), version=version+1
                       WHERE tenant_id=%s AND goal_id=%s AND owner_principal_id=%s""",
                    (tenant_id, goal_id, owner_principal_id),
                )
                return True

    def refresh_outcome(self, *, tenant_id: str, owner_principal_id: str,
                        goal_id: str) -> dict[str, Any]:
        """Recompute outcome from current workflow-run states in one transaction.

        Only succeeded/failed/cancelled are terminal for aggregate purposes.
        Empty or unsettled goals remain active with a NULL outcome. A settled
        non-empty active goal transitions to completed exactly once.
        """
        tenant_id, owner_principal_id = self._scope(tenant_id, owner_principal_id)
        goal_id = self._goal_id(goal_id)
        with self.pool.connection() as conn:
            with conn.transaction():
                goal = self._require_goal(conn, tenant_id, goal_id, owner_principal_id, lock=True)
                rows = conn.execute(
                    """SELECT wr.state
                       FROM workflow_goal_tasks gt
                       JOIN workflow_runs wr ON wr.run_id=gt.task_id
                       WHERE gt.tenant_id=%s AND gt.goal_id=%s
                       ORDER BY gt.created_at, gt.task_id""",
                    (tenant_id, goal_id),
                ).fetchall()
                outcome = aggregate_goal_outcome([row["state"] for row in rows])
                if outcome is not None and goal["state"] == "active":
                    conn.execute(
                        """UPDATE workflow_goals
                           SET state='completed', outcome=%s, updated_at=clock_timestamp(),
                               version=version+1
                           WHERE tenant_id=%s AND goal_id=%s AND owner_principal_id=%s""",
                        (outcome, tenant_id, goal_id, owner_principal_id),
                    )
                    self._event(conn, tenant_id, goal_id, owner_principal_id,
                                "goal.completed", {"outcome": outcome})
                elif goal["state"] == "cancelled":
                    conn.execute(
                        """UPDATE workflow_goals SET outcome=%s, updated_at=clock_timestamp(),
                               version=version+1
                           WHERE tenant_id=%s AND goal_id=%s AND owner_principal_id=%s""",
                        (outcome, tenant_id, goal_id, owner_principal_id),
                    )
                return self._require_goal(conn, tenant_id, goal_id, owner_principal_id)

    def get_goal(self, *, tenant_id: str, owner_principal_id: str,
                 goal_id: str) -> dict[str, Any]:
        tenant_id, owner_principal_id = self._scope(tenant_id, owner_principal_id)
        goal_id = self._goal_id(goal_id)
        with self.pool.connection() as conn:
            return dict(self._require_goal(conn, tenant_id, goal_id, owner_principal_id))

    def list_events(self, *, tenant_id: str, owner_principal_id: str,
                    goal_id: str, after_event_id: int = 0,
                    limit: int = 100) -> list[dict[str, Any]]:
        tenant_id, owner_principal_id = self._scope(tenant_id, owner_principal_id)
        goal_id = self._goal_id(goal_id)
        if type(after_event_id) is not int or after_event_id < 0:
            raise ValueError("after_event_id_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_invalid")
        with self.pool.connection() as conn:
            self._require_goal(conn, tenant_id, goal_id, owner_principal_id)
            rows = conn.execute(
                """SELECT event_id, task_id, event_type, detail_json, created_at
                   FROM workflow_goal_events
                   WHERE tenant_id=%s AND goal_id=%s AND owner_principal_id=%s
                     AND event_id>%s
                   ORDER BY event_id LIMIT %s""",
                (tenant_id, goal_id, owner_principal_id, after_event_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
