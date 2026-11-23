"""Owner-scoped durable project and milestone registry.

Task membership is verified through an injected owner resolver. No global or
implicit task lookup is performed by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Callable
from uuid import uuid4

from core.sqlite_file_security import prepare_private_sqlite_path


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or "\x00" in value:
        raise ValueError("owner_id must be non-empty bounded text.")
    return value.strip()


def _text(value: str, field: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be non-empty bounded text.")
    return value.strip()


def _timestamp(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    owner_id: str
    name: str
    description: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ProjectMilestone:
    milestone_id: str
    project_id: str
    owner_id: str
    title: str
    status: str
    due_at: str | None
    verification_ref: str | None
    version: int


class ProjectRegistry:
    """SQLite project registry; every read and write is scoped to an owner."""

    def __init__(self, database_path: str, *, task_owner_resolver: Callable[[str, str], str | None]):
        if not callable(task_owner_resolver):
            raise ValueError("task_owner_resolver is required.")
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        self._task_owner_resolver = task_owner_resolver
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(owner_id, name)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id, created_at)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS project_task_members (
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, task_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_project_members_owner ON project_task_members(owner_id, project_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS project_milestones (
                    milestone_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open', 'completed')),
                    due_at TEXT,
                    verification_ref TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
                    CHECK(status != 'completed' OR verification_ref IS NOT NULL)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_project_milestones_owner ON project_milestones(owner_id, project_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_project(self, *, owner_id: str, name: str, description: str = "") -> ProjectRecord:
        owner = _owner(owner_id)
        project_name = _text(name, "name", 120)
        if not isinstance(description, str) or len(description) > 2000 or "\x00" in description:
            raise ValueError("description must be bounded text.")
        project_id = "prj_" + uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO projects(project_id, owner_id, name, description, created_at) VALUES(?,?,?,?,?)",
                    (project_id, owner, project_name, description, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("a project with this name already exists for the owner.") from exc
        return ProjectRecord(project_id, owner, project_name, description, now)

    def add_task(self, *, owner_id: str, project_id: str, task_id: str) -> None:
        owner = _owner(owner_id)
        project = _text(project_id, "project_id")
        task = _text(task_id, "task_id")
        # Resolve task ownership outside the project DB transaction; resolver must
        # itself enforce owner-scoped task access and return the immutable owner.
        actual_owner = self._task_owner_resolver(owner, task)
        if actual_owner != owner:
            raise PermissionError("task does not belong to the authenticated owner.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM projects WHERE project_id=? AND owner_id=?", (project, owner)
            ).fetchone() is None:
                raise LookupError("project not found.")
            db.execute(
                "INSERT INTO project_task_members(project_id, owner_id, task_id, added_at) VALUES(?,?,?,?)",
                (project, owner, task, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )

    def remove_task(self, *, owner_id: str, project_id: str, task_id: str) -> bool:
        owner = _owner(owner_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                "DELETE FROM project_task_members WHERE project_id=? AND owner_id=? AND task_id=?",
                (_text(project_id, "project_id"), owner, _text(task_id, "task_id")),
            )
            return cur.rowcount == 1

    def list_projects(self, *, owner_id: str, limit: int = 50) -> tuple[ProjectRecord, ...]:
        owner = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM projects WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                (owner, limit),
            ).fetchall()
            return tuple(ProjectRecord(r["project_id"], r["owner_id"], r["name"], r["description"], r["created_at"]) for r in rows)

    def list_task_ids(self, *, owner_id: str, project_id: str, limit: int = 500) -> tuple[str, ...]:
        owner = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500.")
        with self._connect() as db:
            if db.execute("SELECT 1 FROM projects WHERE project_id=? AND owner_id=?", (project_id, owner)).fetchone() is None:
                raise LookupError("project not found.")
            rows = db.execute(
                "SELECT task_id FROM project_task_members WHERE project_id=? AND owner_id=? ORDER BY added_at LIMIT ?",
                (project_id, owner, limit),
            ).fetchall()
            return tuple(row["task_id"] for row in rows)

    def create_milestone(
        self, *, owner_id: str, project_id: str, title: str, due_at: str | None = None,
    ) -> ProjectMilestone:
        owner = _owner(owner_id)
        project = _text(project_id, "project_id")
        milestone_title = _text(title, "title", 200)
        due = _timestamp(due_at, "due_at")
        milestone_id = "mil_" + uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM projects WHERE project_id=? AND owner_id=?", (project, owner)).fetchone() is None:
                raise LookupError("project not found.")
            db.execute(
                "INSERT INTO project_milestones(milestone_id, project_id, owner_id, title, status, due_at, verification_ref, version, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (milestone_id, project, owner, milestone_title, "open", due, None, 1, now, now),
            )
        return ProjectMilestone(milestone_id, project, owner, milestone_title, "open", due, None, 1)

    def complete_milestone(
        self, *, owner_id: str, milestone_id: str, expected_version: int, verification_ref: str,
    ) -> ProjectMilestone:
        owner = _owner(owner_id)
        milestone = _text(milestone_id, "milestone_id")
        proof = _text(verification_ref, "verification_ref", 500)
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer.")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                "UPDATE project_milestones SET status='completed', verification_ref=?, version=version+1, updated_at=? "
                "WHERE milestone_id=? AND owner_id=? AND status='open' AND version=?",
                (proof, now, milestone, owner, expected_version),
            )
            if cur.rowcount != 1:
                raise LookupError("milestone not found, already completed, or version is stale.")
            row = db.execute(
                "SELECT * FROM project_milestones WHERE milestone_id=? AND owner_id=?",
                (milestone, owner),
            ).fetchone()
            return ProjectMilestone(row["milestone_id"], row["project_id"], row["owner_id"], row["title"],
                                    row["status"], row["due_at"], row["verification_ref"], row["version"])

    def list_milestones(self, *, owner_id: str, project_id: str, limit: int = 100) -> tuple[ProjectMilestone, ...]:
        owner = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100.")
        with self._connect() as db:
            if db.execute("SELECT 1 FROM projects WHERE project_id=? AND owner_id=?", (project_id, owner)).fetchone() is None:
                raise LookupError("project not found.")
            rows = db.execute(
                "SELECT * FROM project_milestones WHERE project_id=? AND owner_id=? ORDER BY created_at LIMIT ?",
                (project_id, owner, limit),
            ).fetchall()
            return tuple(ProjectMilestone(r["milestone_id"], r["project_id"], r["owner_id"], r["title"],
                                          r["status"], r["due_at"], r["verification_ref"], r["version"]) for r in rows)
