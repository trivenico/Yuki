"""Owner-scoped data annotation workbench primitives.

This module stores project rubrics, opaque asset references, task state,
AI-assistance policy metadata and human-confirmed submissions. It deliberately does not
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

from core.annotation_validation import validate_annotation_payload


_TASK_TYPES = frozenset({
    "image_classification", "bounding_boxes", "polygon_segmentation",
    "video_tracking", "audio_transcription", "speaker_diarization",
    "text_classification", "entity_extraction", "response_ranking",
    "llm_evaluation", "custom",
})
_PROJECT_STATES = frozenset({"active", "paused", "completed"})
_AI_ASSISTANCE_MODES = frozenset({"human_only", "permitted_with_disclosure"})
_PROJECT_TRANSITIONS = {
    "active": frozenset({"paused", "completed"}),
    "paused": frozenset({"active", "completed"}),
    "completed": frozenset(),
}
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
    ai_assistance_mode: str
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
class AnnotationDraft:
    task_id: str
    project_id: str
    owner_id: str
    payload: dict[str, Any]
    revision: int
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


class AnnotationPolicyError(PermissionError):
    """The requested assistance mode is prohibited by the project policy."""


class SQLiteAnnotationWorkbench:
    """Durable owner-isolated annotation projects, tasks and review records."""

    def __init__(self, database_path: str, *, max_projects_per_owner: int = 1000):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if type(max_projects_per_owner) is not int or not 1 <= max_projects_per_owner <= 10000:
            raise ValueError("invalid_max_projects_per_owner")
        path = Path(database_path).expanduser()
        if path.is_symlink():
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
                ai_assistance_mode TEXT NOT NULL DEFAULT 'human_only',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_projects_owner
                ON annotation_projects(owner_id, status, updated_at DESC)""")
            project_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(annotation_projects)")
            }
            if "ai_assistance_mode" not in project_columns:
                db.execute(
                    "ALTER TABLE annotation_projects ADD COLUMN "
                    "ai_assistance_mode TEXT NOT NULL DEFAULT 'human_only'"
                )
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
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_drafts (
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES annotation_tasks(task_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_drafts_owner
                ON annotation_drafts(owner_id, project_id, updated_at DESC)""")
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
            for private_path in (
                self.database_path,
                self.database_path + "-wal",
                self.database_path + "-shm",
            ):
                if os.path.exists(private_path):
                    os.chmod(private_path, 0o600)

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
        ai_assistance_mode: str = "human_only",
    ) -> AnnotationProject:
        owner_id = _owner(owner_id)
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError("invalid_project_name")
        if task_type not in _TASK_TYPES:
            raise ValueError("unsupported_annotation_task_type")
        if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 12000:
            raise ValueError("invalid_project_instructions")
        rubric_json = _json_object(rubric, maximum=_MAX_RUBRIC_BYTES, field="rubric")
        if ai_assistance_mode not in _AI_ASSISTANCE_MODES:
            raise ValueError("invalid_ai_assistance_mode")
        now, project_id = _now(), uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM annotation_projects WHERE owner_id=?", (owner_id,)
            ).fetchone()[0]
            if count >= self.max_projects_per_owner:
                raise ValueError("owner_project_capacity_reached")
            db.execute(
                """INSERT INTO annotation_projects
                (project_id, owner_id, name, task_type, instructions, rubric_json,
                 ai_assistance_mode, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (project_id, owner_id, name.strip(), task_type, instructions,
                 rubric_json, ai_assistance_mode, now, now),
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
            if status not in _PROJECT_TRANSITIONS[row["status"]]:
                raise ValueError("invalid_annotation_project_transition")
            if status == "completed":
                unfinished = db.execute(
                    """SELECT COUNT(*) FROM annotation_tasks
                       WHERE owner_id=? AND project_id=?
                         AND status NOT IN ('accepted')""",
                    (owner_id, project_id),
                ).fetchone()[0]
                if unfinished:
                    raise ValueError("annotation_project_has_unreviewed_tasks")
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
                "SELECT status, ai_assistance_mode FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if project is None:
                raise LookupError("annotation_project_not_found")
            if project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            duplicate = db.execute(
                "SELECT 1 FROM annotation_tasks WHERE owner_id=? AND project_id=? AND external_ref=?",
                (owner_id, project_id, external_ref.strip()),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("duplicate_external_task_reference")
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

    def create_tasks(
        self, owner_id: str, project_id: str, *,
        tasks: list[Mapping[str, Any]],
    ) -> list[AnnotationTask]:
        """Atomically import a bounded batch; one invalid row rejects the batch."""
        owner_id = _owner(owner_id)
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 100:
            raise ValueError("invalid_annotation_task_batch_size")
        prepared = []
        seen_refs: set[str] = set()
        for item in tasks:
            if not isinstance(item, Mapping) or set(item) - {"external_ref", "asset_ref", "input_data"}:
                raise ValueError("invalid_annotation_task_batch_row")
            external_ref = item.get("external_ref")
            asset_ref = item.get("asset_ref")
            input_data = item.get("input_data")
            if not isinstance(external_ref, str) or not external_ref.strip() or len(external_ref) > 200:
                raise ValueError("invalid_external_task_reference")
            external_ref = external_ref.strip()
            if external_ref in seen_refs:
                raise ValueError("duplicate_external_task_reference_in_batch")
            seen_refs.add(external_ref)
            if asset_ref is not None and (
                not isinstance(asset_ref, str) or not _ASSET_REF.fullmatch(asset_ref)
            ):
                raise ValueError("asset_ref_must_be_opaque_storage_identifier")
            prepared.append((
                uuid.uuid4().hex, external_ref, asset_ref,
                _json_object(input_data, maximum=_MAX_JSON_BYTES, field="task_input"),
            ))
        now = _now()
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
            if count + len(prepared) > _MAX_TASKS_PER_PROJECT:
                raise ValueError("project_task_capacity_reached")
            refs = [item[1] for item in prepared]
            placeholders = ",".join("?" for _ in refs)
            duplicate = db.execute(
                "SELECT 1 FROM annotation_tasks WHERE owner_id=? AND project_id=? "
                f"AND external_ref IN ({placeholders}) LIMIT 1",
                [owner_id, project_id, *refs],
            ).fetchone()
            if duplicate is not None:
                raise ValueError("duplicate_external_task_reference")
            rows = []
            for task_id, external_ref, asset_ref, input_json in prepared:
                db.execute(
                    """INSERT INTO annotation_tasks VALUES
                    (?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)""",
                    (task_id, project_id, owner_id, external_ref, asset_ref,
                     input_json, now, now),
                )
                self._audit(db, owner_id, project_id, task_id, "task_created", {
                    "batch": True,
                })
                rows.append(db.execute(
                    "SELECT * FROM annotation_tasks WHERE task_id=?", (task_id,)
                ).fetchone())
        return [self._task(row) for row in rows]

    def export_accepted(
        self, owner_id: str, project_id: str, *, limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return only accepted work for an owner-controlled export pipeline."""
        owner_id = _owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("invalid_export_limit")
        with self._connect() as db:
            rows = db.execute(
                """SELECT t.external_ref, t.asset_ref, t.input_json,
                          s.payload_json, s.ai_assisted, s.updated_at AS accepted_at
                   FROM annotation_tasks AS t
                   JOIN annotation_submissions AS s
                     ON s.task_id=t.task_id AND s.owner_id=t.owner_id
                   WHERE t.owner_id=? AND t.project_id=? AND s.status='accepted'
                   ORDER BY s.updated_at, s.submission_id LIMIT ?""",
                (owner_id, project_id, limit),
            ).fetchall()
        return [{
            "external_ref": row["external_ref"],
            "asset_ref": row["asset_ref"],
            "input_data": _decode(row["input_json"]),
            "annotation": _decode(row["payload_json"]),
            "ai_assisted": bool(row["ai_assisted"]),
            "accepted_at": row["accepted_at"],
        } for row in rows]

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

    def get_draft(self, owner_id: str, task_id: str) -> AnnotationDraft | None:
        owner_id = _owner(owner_id)
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_task_id")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM annotation_drafts WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
        if row is None:
            return None
        return AnnotationDraft(
            task_id=row["task_id"], project_id=row["project_id"],
            owner_id=row["owner_id"], payload=_decode(row["payload_json"]),
            revision=row["revision"], created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_draft(
        self, owner_id: str, task_id: str, *, payload: Mapping[str, Any],
        expected_revision: int,
    ) -> AnnotationDraft | None:
        owner_id = _owner(owner_id)
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_task_id")
        if type(expected_revision) is not int or not 0 <= expected_revision < 2_147_483_647:
            raise ValueError("invalid_expected_revision")
        payload_json = _json_object(payload, maximum=_MAX_JSON_BYTES, field="annotation_draft")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute(
                "SELECT project_id, status FROM annotation_tasks WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
            if task is None:
                return None
            if task["status"] not in {"in_progress", "rework"}:
                raise ValueError("annotation_task_not_editable")
            project = db.execute(
                "SELECT status FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, task["project_id"]),
            ).fetchone()
            if project is None or project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            current = db.execute(
                "SELECT revision, created_at FROM annotation_drafts WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
            if current is None:
                if expected_revision != 0:
                    raise AnnotationConflict("annotation_draft_revision_conflict")
                revision, created_at = 1, now
                db.execute(
                    "INSERT INTO annotation_drafts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (task_id, task["project_id"], owner_id, payload_json, revision, created_at, now),
                )
            else:
                if current["revision"] != expected_revision:
                    raise AnnotationConflict("annotation_draft_revision_conflict")
                revision, created_at = expected_revision + 1, current["created_at"]
                db.execute(
                    "UPDATE annotation_drafts SET payload_json=?, revision=?, updated_at=? "
                    "WHERE owner_id=? AND task_id=? AND revision=?",
                    (payload_json, revision, now, owner_id, task_id, expected_revision),
                )
            self._audit(db, owner_id, task["project_id"], task_id, "draft_saved",
                        {"revision": revision})
            row = db.execute(
                "SELECT * FROM annotation_drafts WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
        return AnnotationDraft(
            task_id=row["task_id"], project_id=row["project_id"],
            owner_id=row["owner_id"], payload=_decode(row["payload_json"]),
            revision=row["revision"], created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def delete_draft(
        self, owner_id: str, task_id: str, *, expected_revision: int,
    ) -> bool:
        owner_id = _owner(owner_id)
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_task_id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("invalid_expected_revision")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT project_id, revision FROM annotation_drafts WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
            if row is None:
                return False
            if row["revision"] != expected_revision:
                raise AnnotationConflict("annotation_draft_revision_conflict")
            db.execute(
                "DELETE FROM annotation_drafts WHERE owner_id=? AND task_id=? AND revision=?",
                (owner_id, task_id, expected_revision),
            )
            self._audit(db, owner_id, row["project_id"], task_id, "draft_deleted",
                        {"revision": expected_revision})
        return True

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
                "SELECT status, ai_assistance_mode, task_type, rubric_json FROM annotation_projects "
                "WHERE owner_id=? AND project_id=?",
                (owner_id, task["project_id"]),
            ).fetchone()
            if project is None or project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            validate_annotation_payload(
                project["task_type"], payload, _decode(project["rubric_json"])
            )
            if ai_assisted and project["ai_assistance_mode"] != "permitted_with_disclosure":
                raise AnnotationPolicyError("ai_assistance_not_permitted_for_project")
            db.execute(
                """INSERT INTO annotation_submissions VALUES
                (?, ?, ?, ?, ?, ?, 1, 'submitted', '', ?, ?)""",
                (submission_id, task_id, task["project_id"], owner_id,
                 payload_json, int(ai_assisted), now, now),
            )
            db.execute(
                "DELETE FROM annotation_drafts WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
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
