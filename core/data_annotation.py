"""Owner-scoped data annotation workbench primitives.

This module stores project rubrics, opaque asset references, task state,
AI-assisted drafts and human-confirmed submissions. It deliberately does not
fetch remote assets, scrape marketplaces, claim worker identity, or submit work
to a third-party platform. External delivery is a separate approval-gated step.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Mapping


_TASK_TYPES = frozenset({
    "image_classification", "bounding_boxes", "polygon_segmentation",
    "video_tracking", "audio_transcription", "speaker_diarization",
    "text_classification", "entity_extraction", "response_ranking",
    "llm_evaluation", "custom",
})
_PROJECT_STATES = frozenset({"active", "paused", "completed"})
_TASK_STATES = frozenset({"pending", "in_progress", "submitted", "accepted", "rework"})
_ID = re.compile(r"^[0-9a-f]{32}$")
_ASSET_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_JSON_BYTES = 64 * 1024
_MAX_RUBRIC_BYTES = 32 * 1024
_MAX_TASKS_PER_PROJECT = 100_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("invalid_owner_id")
    return value


def _json_object(value: Any, *, maximum: int, field: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_" + field)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid_" + field) from None
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError(field + "_too_large")
    return encoded


def _decode(value: str) -> dict[str, Any]:
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("stored_annotation_payload_invalid")
    return result


@dataclass(frozen=True)
class AnnotationProject:
    project_id: str
    owner_id: str
    name: str
    task_type: str
    instructions: str
    rubric: dict[str, Any]
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AnnotationTask:
    task_id: str
    project_id: str
    owner_id: str
    external_ref: str
    asset_ref: str | None
    input_data: dict[str, Any]
    status: str
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AnnotationSubmission:
    submission_id: str
    task_id: str
    project_id: str
    owner_id: str
    payload: dict[str, Any]
    ai_assisted: bool
    human_confirmed: bool
    status: str
    reviewer_notes: str
    created_at: str
    updated_at: str


class AnnotationConflict(RuntimeError):
    """The task changed since the caller last read it."""


class SQLiteAnnotationWorkbench:
    """Durable owner-isolated annotation projects, tasks and review records."""

    def __init__(self, database_path: str, *, max_projects_per_owner: int = 1000):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if type(max_projects_per_owner) is not int or not 1 <= max_projects_per_owner <= 10000:
            raise ValueError("invalid_max_projects_per_owner")
        path = Path(database_path).expanduser()
        if path.exists() and path.is_symlink():
            raise ValueError("database_path_must_not_be_symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(path.resolve())
        self.max_projects_per_owner = max_projects_per_owner
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_projects (
                project_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                task_type TEXT NOT NULL,
                instructions TEXT NOT NULL,
                rubric_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_projects_owner
                ON annotation_projects(owner_id, status, updated_at DESC)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_tasks (
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                external_ref TEXT NOT NULL,
                asset_ref TEXT,
                input_json TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, project_id, external_ref),
                FOREIGN KEY(project_id) REFERENCES annotation_projects(project_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_tasks_owner_state
                ON annotation_tasks(owner_id, project_id, status, created_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_submissions (
                submission_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                ai_assisted INTEGER NOT NULL,
                human_confirmed INTEGER NOT NULL,
                status TEXT NOT NULL,
                reviewer_notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES annotation_tasks(task_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_submissions_task
                ON annotation_submissions(owner_id, task_id, created_at DESC)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_audit (
                event_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT,
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_audit_owner
                ON annotation_audit(owner_id, created_at DESC)""")
        if os.name == "posix":
            os.chmod(self.database_path, 0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _project(row) -> AnnotationProject | None:
        if row is None:
            return None
        value = dict(row)
        value["rubric"] = _decode(value.pop("rubric_json"))
        return AnnotationProject(**value)

    @staticmethod
    def _task(row) -> AnnotationTask | None:
        if row is None:
            return None
        value = dict(row)
        value["input_data"] = _decode(value.pop("input_json"))
        return AnnotationTask(**value)

    @staticmethod
    def _submission(row) -> AnnotationSubmission | None:
        if row is None:
            return None
        value = dict(row)
        value["payload"] = _decode(value.pop("payload_json"))
        value["ai_assisted"] = bool(value["ai_assisted"])
        value["human_confirmed"] = bool(value["human_confirmed"])
        return AnnotationSubmission(**value)

    @staticmethod
    def _audit(db, owner_id, project_id, task_id, event_type, details):
        db.execute(
            """INSERT INTO annotation_audit
            (event_id, owner_id, project_id, task_id, event_type, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, owner_id, project_id, task_id, event_type,
             _json_object(details, maximum=4096, field="audit_details"), _now()),
        )

    def create_project(
        self, owner_id: str, *, name: str, task_type: str,
        instructions: str, rubric: Mapping[str, Any],
    ) -> AnnotationProject:
        owner_id = _owner(owner_id)
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError("invalid_project_name")
        if task_type not in _TASK_TYPES:
            raise ValueError("unsupported_annotation_task_type")
        if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 12000:
            raise ValueError("invalid_project_instructions")
        rubric_json = _json_object(rubric, maximum=_MAX_RUBRIC_BYTES, field="rubric")
        now, project_id = _now(), uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM annotation_projects WHERE owner_id=?", (owner_id,)
            ).fetchone()[0]
            if count >= self.max_projects_per_owner:
                raise ValueError("owner_project_capacity_reached")
            db.execute(
                """INSERT INTO annotation_projects VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (project_id, owner_id, name.strip(), task_type, instructions,
                 rubric_json, now, now),
            )
            self._audit(db, owner_id, project_id, None, "project_created",
                        {"task_type": task_type})
            row = db.execute(
                "SELECT * FROM annotation_projects WHERE project_id=?", (project_id,)
            ).fetchone()
        return self._project(row)

    def get_project(self, owner_id: str, project_id: str) -> AnnotationProject | None:
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
        return self._project(row)

    def list_projects(self, owner_id: str, *, limit: int = 100) -> list[AnnotationProject]:
        owner_id = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_limit")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM annotation_projects WHERE owner_id=? "
                "ORDER BY updated_at DESC, project_id LIMIT ?", (owner_id, limit)
            ).fetchall()
        return [self._project(row) for row in rows]

    def set_project_status(
        self, owner_id: str, project_id: str, *, status: str,
    ) -> AnnotationProject | None:
        owner_id = _owner(owner_id)
        if status not in _PROJECT_STATES:
            raise ValueError("invalid_project_status")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE annotation_projects SET status=?, updated_at=? "
                "WHERE owner_id=? AND project_id=?",
                (status, now, owner_id, project_id),
            )
            self._audit(db, owner_id, project_id, None, "project_status_changed",
                        {"status": status})
            updated = db.execute(
                "SELECT * FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
        return self._project(updated)

    def create_task(
        self, owner_id: str, project_id: str, *, external_ref: str,
        input_data: Mapping[str, Any], asset_ref: str | None = None,
    ) -> AnnotationTask:
        owner_id = _owner(owner_id)
        if not isinstance(external_ref, str) or not external_ref.strip() or len(external_ref) > 200:
            raise ValueError("invalid_external_task_reference")
        if asset_ref is not None and (
            not isinstance(asset_ref, str) or not _ASSET_REF.fullmatch(asset_ref)
        ):
            raise ValueError("asset_ref_must_be_opaque_storage_identifier")
        input_json = _json_object(input_data, maximum=_MAX_JSON_BYTES, field="task_input")
        now, task_id = _now(), uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute(
                "SELECT status FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if project is None:
                raise LookupError("annotation_project_not_found")
            if project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            count = db.execute(
                "SELECT COUNT(*) FROM annotation_tasks WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()[0]
            if count >= _MAX_TASKS_PER_PROJECT:
                raise ValueError("project_task_capacity_reached")
            db.execute(
                """INSERT INTO annotation_tasks VALUES
                (?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)""",
                (task_id, project_id, owner_id, external_ref.strip(), asset_ref,
                 input_json, now, now),
            )
            self._audit(db, owner_id, project_id, task_id, "task_created", {})
            row = db.execute(
                "SELECT * FROM annotation_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return self._task(row)

    def get_task(self, owner_id: str, task_id: str) -> AnnotationTask | None:
        owner_id = _owner(owner_id)
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM annotation_tasks WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
        return self._task(row)

    def list_tasks(
        self, owner_id: str, project_id: str, *,
        status: str | None = None, limit: int = 100,
    ) -> list[AnnotationTask]:
        owner_id = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_limit")
        if status is not None and status not in _TASK_STATES:
            raise ValueError("invalid_task_status")
        clauses = ["owner_id=?", "project_id=?"]
        args: list[Any] = [owner_id, project_id]
        if status is not None:
            clauses.append("status=?")
            args.append(status)
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM annotation_tasks WHERE " + " AND ".join(clauses)
                + " ORDER BY created_at, task_id LIMIT ?", args,
            ).fetchall()
        return [self._task(row) for row in rows]

    def claim_task(self, owner_id: str, task_id: str, *, expected_version: int) -> AnnotationTask | None:
        owner_id = _owner(owner_id)
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("invalid_expected_version")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM annotation_tasks WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
            if row is None:
                return None
            if row["version"] != expected_version:
                raise AnnotationConflict("annotation_task_version_conflict")
            if row["status"] not in {"pending", "rework", "in_progress"}:
                raise ValueError("annotation_task_not_claimable")
            db.execute(
                "UPDATE annotation_tasks SET status='in_progress', version=version+1, updated_at=? "
                "WHERE owner_id=? AND task_id=? AND version=?",
                (now, owner_id, task_id, expected_version),
            )
            self._audit(db, owner_id, row["project_id"], task_id, "task_claimed", {})
            updated = db.execute(
                "SELECT * FROM annotation_tasks WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
        return self._task(updated)

    def submit(
        self, owner_id: str, task_id: str, *, payload: Mapping[str, Any],
        expected_version: int, ai_assisted: bool = False,
        human_confirmed: bool = False,
    ) -> AnnotationSubmission | None:
        owner_id = _owner(owner_id)
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("invalid_expected_version")
        if type(ai_assisted) is not bool or type(human_confirmed) is not bool:
            raise ValueError("invalid_submission_flags")
        if not human_confirmed:
            raise PermissionError("human_confirmation_required_before_submission")
        payload_json = _json_object(payload, maximum=_MAX_JSON_BYTES, field="annotation_payload")
        now, submission_id = _now(), uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute(
                "SELECT * FROM annotation_tasks WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
            if task is None:
                return None
            if task["version"] != expected_version:
                raise AnnotationConflict("annotation_task_version_conflict")
            if task["status"] not in {"in_progress", "rework"}:
                raise ValueError("annotation_task_not_submittable")
            project = db.execute(
                "SELECT status FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, task["project_id"]),
            ).fetchone()
            if project is None or project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            db.execute(
                """INSERT INTO annotation_submissions VALUES
                (?, ?, ?, ?, ?, ?, 1, 'submitted', '', ?, ?)""",
                (submission_id, task_id, task["project_id"], owner_id,
                 payload_json, int(ai_assisted), now, now),
            )
            db.execute(
                "UPDATE annotation_tasks SET status='submitted', version=version+1, updated_at=? "
                "WHERE owner_id=? AND task_id=? AND version=?",
                (now, owner_id, task_id, expected_version),
            )
            self._audit(db, owner_id, task["project_id"], task_id, "submission_created",
                        {"submission_id": submission_id, "ai_assisted": ai_assisted})
            row = db.execute(
                "SELECT * FROM annotation_submissions WHERE submission_id=?",
                (submission_id,),
            ).fetchone()
        return self._submission(row)

    def review(
        self, owner_id: str, submission_id: str, *,
        decision: str, notes: str = "",
    ) -> AnnotationSubmission | None:
        owner_id = _owner(owner_id)
        if decision not in {"accepted", "rework"}:
            raise ValueError("invalid_review_decision")
        if not isinstance(notes, str) or len(notes) > 4000:
            raise ValueError("invalid_review_notes")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM annotation_submissions WHERE owner_id=? AND submission_id=?",
                (owner_id, submission_id),
            ).fetchone()
            if row is None:
                return None
            if row["status"] != "submitted":
                raise ValueError("submission_already_reviewed")
            db.execute(
                "UPDATE annotation_submissions SET status=?, reviewer_notes=?, updated_at=? "
                "WHERE owner_id=? AND submission_id=? AND status='submitted'",
                (decision, notes, now, owner_id, submission_id),
            )
            db.execute(
                "UPDATE annotation_tasks SET status=?, version=version+1, updated_at=? "
                "WHERE owner_id=? AND task_id=? AND status='submitted'",
                (decision, now, owner_id, row["task_id"]),
            )
            self._audit(db, owner_id, row["project_id"], row["task_id"],
                        "submission_reviewed", {"decision": decision})
            updated = db.execute(
                "SELECT * FROM annotation_submissions WHERE owner_id=? AND submission_id=?",
                (owner_id, submission_id),
            ).fetchone()
        return self._submission(updated)

    def list_submissions(
        self, owner_id: str, task_id: str, *, limit: int = 50,
    ) -> list[AnnotationSubmission]:
        owner_id = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM annotation_submissions WHERE owner_id=? AND task_id=? "
                "ORDER BY created_at DESC, submission_id LIMIT ?",
                (owner_id, task_id, limit),
            ).fetchall()
        return [self._submission(row) for row in rows]

    def audit_events(self, owner_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        owner_id = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_limit")
        with self._connect() as db:
            rows = db.execute(
                "SELECT project_id, task_id, event_type, details_json, created_at "
                "FROM annotation_audit WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [{
            "project_id": row["project_id"], "task_id": row["task_id"],
            "event_type": row["event_type"], "details": _decode(row["details_json"]),
            "created_at": row["created_at"],
        } for row in rows]
