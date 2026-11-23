"""Append-only project/task bindings to exact immutable execution spec versions."""
from __future__ import annotations

import sqlite3
from typing import Callable

from core.sqlite_file_security import prepare_private_sqlite_path

from core.execution_spec_registry import ExecutionSpecRegistry


class ProjectExecutionSpecBindings:
    """Bind a project task to an exact owner-scoped spec version after host checks."""

    def __init__(
        self, database_path: str, *, spec_registry: ExecutionSpecRegistry,
        authorize_project: Callable[[str, str, str], bool],
        task_is_member: Callable[[str, str, str], bool],
    ):
        if not isinstance(spec_registry, ExecutionSpecRegistry):
            raise TypeError("spec_registry must be an ExecutionSpecRegistry.")
        if not callable(authorize_project) or not callable(task_is_member):
            raise TypeError("project authorization and membership resolvers are required.")
        if not isinstance(database_path, str) or not database_path.strip() or database_path == ":memory:":
            raise ValueError("a durable file-backed database path is required.")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.specs = spec_registry
        self.authorize_project = authorize_project
        self.task_is_member = task_is_member
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS project_execution_spec_bindings (
                owner_id TEXT NOT NULL, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
                binding_version INTEGER NOT NULL, spec_id TEXT NOT NULL, spec_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(owner_id, project_id, task_id, binding_version))""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS project_spec_bindings_no_update
                BEFORE UPDATE ON project_execution_spec_bindings BEGIN SELECT RAISE(ABORT, "immutable project spec binding"); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS project_spec_bindings_no_delete
                BEFORE DELETE ON project_execution_spec_bindings BEGIN SELECT RAISE(ABORT, "immutable project spec binding"); END""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @staticmethod
    def _text(value: str, field: str) -> str:
        if type(value) is not str or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
            raise ValueError(f"{field} must be bounded non-empty text.")
        return value.strip()

    def _authorize(self, actor_id: str, owner_id: str, project_id: str, task_id: str, action: str) -> None:
        actor = self._text(actor_id, "actor_id")
        owner = self._text(owner_id, "owner_id")
        project = self._text(project_id, "project_id")
        task = self._text(task_id, "task_id")
        if self.authorize_project(actor, owner, project) is not True:
            raise PermissionError("project execution spec access denied.")
        if self.task_is_member(owner, project, task) is not True:
            raise LookupError("task is not a member of this project.")
        if action not in ("read", "write"):
            raise ValueError("unsupported action.")

    def bind(self, *, actor_id: str, owner_id: str, project_id: str, task_id: str, spec_id: str, spec_version: int) -> int:
        actor_id = self._text(actor_id, "actor_id")
        owner_id = self._text(owner_id, "owner_id")
        project_id = self._text(project_id, "project_id")
        task_id = self._text(task_id, "task_id")
        self._authorize(actor_id, owner_id, project_id, task_id, "write")
        spec_key = self._text(spec_id, "spec_id")
        if type(spec_version) is not int or spec_version < 1:
            raise ValueError("spec_version must be a positive integer.")
        spec = self.specs.get(actor_id=actor_id, owner_id=owner_id, spec_id=spec_key, version=spec_version)
        if spec is None:
            raise LookupError("execution spec version not found.")
        from datetime import datetime, timezone
        created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT MAX(binding_version) AS version FROM project_execution_spec_bindings WHERE owner_id=? AND project_id=? AND task_id=?", (owner_id, project_id, task_id)).fetchone()
            next_version = (row["version"] or 0) + 1
            db.execute("INSERT INTO project_execution_spec_bindings VALUES(?,?,?,?,?,?,?)", (owner_id, project_id, task_id, next_version, spec_key, spec_version, created))
        return next_version

    def get(self, *, actor_id: str, owner_id: str, project_id: str, task_id: str) -> tuple[str, int] | None:
        actor_id = self._text(actor_id, "actor_id")
        owner_id = self._text(owner_id, "owner_id")
        project_id = self._text(project_id, "project_id")
        task_id = self._text(task_id, "task_id")
        self._authorize(actor_id, owner_id, project_id, task_id, "read")
        with self._connect() as db:
            row = db.execute("SELECT spec_id, spec_version FROM project_execution_spec_bindings WHERE owner_id=? AND project_id=? AND task_id=? ORDER BY binding_version DESC LIMIT 1", (owner_id, project_id, task_id)).fetchone()
        return (row["spec_id"], row["spec_version"]) if row else None

