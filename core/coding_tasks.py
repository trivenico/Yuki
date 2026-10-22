"""Durable, approval-gated coding task plans inspired by coding-agent workflows.

This module stores reviewable plans and patch metadata. It deliberately does not
run shell commands, apply patches, commit, push, or merge changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any


class CodingTaskError(RuntimeError):
    """Base exception for invalid or conflicting coding-task operations."""


class CodingTaskConflict(CodingTaskError):
    """Raised when an idempotency key or state version conflicts."""


class CodingTaskTransitionError(CodingTaskError):
    """Raised when a task transition is not permitted."""


_STATES = frozenset({"draft", "ready_for_review", "approved", "completed", "rejected", "cancelled"})
_TRANSITIONS = {
    "draft": frozenset({"ready_for_review", "cancelled"}),
    "ready_for_review": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"completed", "cancelled"}),
    "completed": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
}
_MAX_TEXT = 20_000
_MAX_PATCH = 256 * 1024


@dataclass(frozen=True)
class CodingTask:
    task_id: str
    idempotency_key: str
    goal: str
    workspace: str
    state: str
    plan: list[dict[str, str]]
    patch: str | None
    patch_sha256: str | None
    review: str | None
    created_at: str
    updated_at: str
    version: int


class CodingTaskStore:
    """SQLite store for task plans and human-reviewed patch proposals."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS coding_tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    goal TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    state TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    patch TEXT,
                    patch_sha256 TEXT,
                    review TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_coding_tasks_state ON coding_tasks(state, updated_at)")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @staticmethod
    def _bounded_text(value: Any, name: str, limit: int = _MAX_TEXT) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise ValueError(f"{name} must be non-empty text within the configured size limit.")
        return value.strip()

    @staticmethod
    def _record(row) -> CodingTask:
        return CodingTask(
            task_id=row["task_id"], idempotency_key=row["idempotency_key"],
            goal=row["goal"], workspace=row["workspace"], state=row["state"],
            plan=json.loads(row["plan_json"]), patch=row["patch"],
            patch_sha256=row["patch_sha256"], review=row["review"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            version=row["version"],
        )

    def create(self, *, idempotency_key: str, goal: str, workspace: str) -> CodingTask:
        key = self._bounded_text(idempotency_key, "idempotency_key", 200)
        goal = self._bounded_text(goal, "goal", 4000)
        workspace = self._bounded_text(workspace, "workspace", 4096)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM coding_tasks WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                task = self._record(existing)
                if task.goal != goal or task.workspace != workspace:
                    raise CodingTaskConflict("Idempotency key already used for a different task.")
                return task
            task_id = secrets.token_urlsafe(18)
            db.execute(
                "INSERT INTO coding_tasks VALUES (?, ?, ?, ?, 'draft', '[]', NULL, NULL, NULL, ?, ?, 0)",
                (task_id, key, goal, workspace, now, now),
            )
        # Read back only after the INSERT transaction has committed.
        return self.get(task_id)

    def get(self, task_id: str) -> CodingTask:
        if not isinstance(task_id, str) or not task_id or "\x00" in task_id:
            raise ValueError("task_id must be a valid string.")
        with self._connect() as db:
            row = db.execute("SELECT * FROM coding_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("Coding task not found.")
        return self._record(row)

    def list_tasks(self, *, states: tuple[str, ...] = (), limit: int = 50) -> list[CodingTask]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        if any(state not in _STATES for state in states):
            raise ValueError("Unknown task state.")
        with self._connect() as db:
            if states:
                placeholders = ",".join("?" for _ in states)
                rows = db.execute(
                    f"SELECT * FROM coding_tasks WHERE state IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?",
                    (*states, limit),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM coding_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._record(row) for row in rows]

    def update_plan(self, task_id: str, *, expected_version: int, plan: list[dict[str, str]]) -> CodingTask:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        if not isinstance(plan, list) or len(plan) > 50:
            raise ValueError("plan must contain at most 50 steps.")
        clean = []
        for step in plan:
            if not isinstance(step, dict) or set(step) - {"title", "description"}:
                raise ValueError("Each plan step must contain only title and description.")
            title = self._bounded_text(step.get("title"), "step title", 200)
            description = self._bounded_text(step.get("description"), "step description", 2000)
            clean.append({"title": title, "description": description})
        return self._mutate(task_id, expected_version, "draft", "draft",
                            {"plan_json": json.dumps(clean, ensure_ascii=True)})

    def attach_patch(self, task_id: str, *, expected_version: int, patch: str) -> CodingTask:
        if not isinstance(patch, str) or not patch or len(patch.encode("utf-8")) > _MAX_PATCH or "\x00" in patch:
            raise ValueError("patch must be non-empty text no larger than 256 KiB and NUL-free.")
        if not patch.startswith("diff --git "):
            raise ValueError("patch must be a unified Git diff beginning with 'diff --git '.")
        digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        return self._mutate(task_id, expected_version, "draft", "draft",
                            {"patch": patch, "patch_sha256": digest, "review": None})

    def transition(self, task_id: str, *, expected_version: int, new_state: str,
                   review: str | None = None) -> CodingTask:
        if new_state not in _STATES:
            raise ValueError("Unknown coding task state.")
        current = self.get(task_id)
        if current.version != expected_version:
            raise CodingTaskConflict("Task version changed; reload before retrying.")
        if new_state not in _TRANSITIONS[current.state]:
            raise CodingTaskTransitionError(f"Transition {current.state!r} -> {new_state!r} is not permitted.")
        updates = {"review": review}
        if review is not None:
            updates["review"] = self._bounded_text(review, "review", 8000)
        return self._mutate(task_id, expected_version, current.state, new_state, updates)

    def _mutate(self, task_id: str, expected_version: int, expected_state: str,
                new_state: str, updates: dict[str, Any]) -> CodingTask:
        now = datetime.now(timezone.utc).isoformat()
        allowed_columns = {"state", "plan_json", "patch", "patch_sha256", "review", "updated_at", "version"}
        if set(updates) - allowed_columns:
            raise ValueError("Unsupported update field.")
        with self._connect() as db:
            row = db.execute("SELECT state, version FROM coding_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("Coding task not found.")
            if row["version"] != expected_version or row["state"] != expected_state:
                raise CodingTaskConflict("Task changed concurrently; reload before retrying.")
            fields = dict(updates)
            fields.update({"state": new_state, "updated_at": now, "version": expected_version + 1})
            assignments = ", ".join(f"{name}=?" for name in fields)
            cur = db.execute(
                f"UPDATE coding_tasks SET {assignments} WHERE task_id=? AND version=? AND state=?",
                (*fields.values(), task_id, expected_version, expected_state),
            )
            if cur.rowcount != 1:
                raise CodingTaskConflict("Task changed concurrently; reload before retrying.")
        return self.get(task_id)

    def patch_integrity_ok(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task.patch is None or task.patch_sha256 is None:
            return False
        return secrets.compare_digest(hashlib.sha256(task.patch.encode("utf-8")).hexdigest(), task.patch_sha256)

    def delete(self, task_id: str, *, expected_version: int) -> None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer.")
        with self._connect() as db:
            cur = db.execute("DELETE FROM coding_tasks WHERE task_id=? AND version=?", (task_id, expected_version))
            if cur.rowcount != 1:
                raise CodingTaskConflict("Task changed or does not exist.")
