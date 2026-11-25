"""Shared PostgreSQL adapter for specialist-agent routing manifests.

This registry stores bounded metadata only. It is deliberately independent of
agent plans, task state, executable code, and authorization decisions.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from core.postgres_database_identity import postgres_database_identity


class PostgresSpecialistAgentRegistry:
    """PostgreSQL authority for manifests shared by distributed coordinators."""

    REQUIRED_COLUMNS = {
        "agent_id", "name", "capabilities_json", "action", "permissions_json",
        "enabled", "manifest_revision", "created_at", "updated_at",
    }
    REQUIRED_CONSTRAINTS = {
        "specialist_agent_registry_id_check",
        "specialist_agent_registry_name_check",
        "specialist_agent_registry_action_check",
        "specialist_agent_registry_capabilities_check",
        "specialist_agent_registry_permissions_check",
        "specialist_agent_registry_revision_check",
    }

    def __init__(
        self,
        database_url: str,
        *,
        agent_factory: Callable[..., Any],
        validate_agent: Callable[[Any], None],
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        pool_timeout: float = 10.0,
    ) -> None:
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("specialist_registry_database_url_invalid")
        if not callable(agent_factory) or not callable(validate_agent):
            raise TypeError("agent_factory and validate_agent must be callable")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("specialist_registry_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("specialist_registry_pool_timeout_invalid")
        self.database_identity = postgres_database_identity(database_url)
        self._agent_factory = agent_factory
        self._validate_agent = validate_agent
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=float(pool_timeout),
            kwargs={"row_factory": dict_row},
            open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
            self.check_ready()
        except Exception:
            self.pool.close()
            raise

    def check_ready(self) -> None:
        with self.pool.connection() as conn:
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='specialist_agent_registry'"
            ).fetchall()
            constraints = conn.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid=to_regclass('specialist_agent_registry')"
            ).fetchall()
        actual_columns = {row["column_name"] for row in columns}
        actual_constraints = {row["conname"] for row in constraints}
        missing_columns = self.REQUIRED_COLUMNS - actual_columns
        missing_constraints = self.REQUIRED_CONSTRAINTS - actual_constraints
        if missing_columns or missing_constraints:
            raise RuntimeError(
                "specialist_registry_schema_not_ready:"
                + ",".join(sorted(missing_columns | missing_constraints))
            )

    def check_ready(self) -> bool:
        """Revalidate the installed schema before a host accepts protected work."""
        self.check_schema()
        return True

    def check_schema(self) -> None:
        """Fail closed unless all registry columns and database constraints exist."""
        with self.pool.connection() as conn:
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='specialist_agent_registry'"
            ).fetchall()
            constraints = conn.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid=to_regclass('specialist_agent_registry')"
            ).fetchall()
        actual_columns = {row["column_name"] for row in columns}
        actual_constraints = {row["conname"] for row in constraints}
        missing_columns = self.REQUIRED_COLUMNS - actual_columns
        missing_constraints = self.REQUIRED_CONSTRAINTS - actual_constraints
        if missing_columns or missing_constraints:
            raise RuntimeError(
                "specialist_registry_schema_not_ready:"
                + ",".join(sorted(missing_columns | missing_constraints))
            )

    def _from_row(self, row: dict[str, Any]) -> Any:
        try:
            capabilities = row["capabilities_json"]
            permissions = row["permissions_json"]
            if isinstance(capabilities, str):
                capabilities = json.loads(capabilities)
            if isinstance(permissions, str):
                permissions = json.loads(permissions)
            if (
                not isinstance(capabilities, list)
                or not isinstance(permissions, list)
                or any(not isinstance(value, str) for value in capabilities + permissions)
                or len(capabilities) > 32 or len(permissions) > 32
                or len(set(capabilities)) != len(capabilities)
                or len(set(permissions)) != len(permissions)
                or type(row["enabled"]) is not bool
                or type(row["manifest_revision"]) is not int
                or row["manifest_revision"] < 1
            ):
                raise ValueError("invalid specialist manifest row")
            agent = self._agent_factory(
                agent_id=row["agent_id"],
                name=row["name"],
                capabilities=frozenset(capabilities),
                action=row["action"],
                permissions=frozenset(permissions),
                enabled=row["enabled"],
            )
            self._validate_agent(agent)
            return agent
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("persisted specialist registry contains an invalid manifest.") from exc

    @staticmethod
    def _values(agent: Any) -> tuple[Any, ...]:
        return (
            agent.agent_id,
            agent.name,
            Jsonb(sorted(agent.capabilities)),
            agent.action,
            Jsonb(sorted(agent.permissions)),
            agent.enabled,
        )

    def initialize(self, agents: Iterable[Any]) -> None:
        """Seed missing defaults only; never overwrite an operator-managed row."""
        values = tuple(agents)
        for agent in values:
            self._validate_agent(agent)
        with self.pool.connection() as conn:
            for agent in values:
                conn.execute(
                    """
                    INSERT INTO specialist_agent_registry(
                        agent_id, name, capabilities_json, action, permissions_json, enabled
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (agent_id) DO NOTHING
                    """,
                    self._values(agent),
                )

    def list_agents(self) -> tuple[Any, ...]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM specialist_agent_registry ORDER BY agent_id"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def register_agent(self, agent: Any) -> None:
        self._validate_agent(agent)
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO specialist_agent_registry(
                        agent_id, name, capabilities_json, action, permissions_json, enabled
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    self._values(agent),
                )
        except UniqueViolation as exc:
            raise ValueError("agent ID is already registered.") from exc

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> Any:
        if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 80:
            raise ValueError("agent_id must be a bounded identifier.")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE specialist_agent_registry
                SET enabled=%s,
                    manifest_revision=manifest_revision + 1,
                    updated_at=clock_timestamp()
                WHERE agent_id=%s AND enabled IS DISTINCT FROM %s
                RETURNING *
                """,
                (enabled, agent_id, enabled),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM specialist_agent_registry WHERE agent_id=%s",
                    (agent_id,),
                ).fetchone()
            if row is None:
                raise KeyError(agent_id)
            return self._from_row(row)

    def get_manifest_revision(self, agent_id: str) -> int:
        """Expose the persisted revision for health checks and concurrency tests."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT manifest_revision FROM specialist_agent_registry WHERE agent_id=%s",
                (agent_id,),
            ).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return int(row["manifest_revision"])

    def close(self) -> None:
        self.pool.close()
