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
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Mapping

from core.annotation_validation import validate_annotation_payload
from core.annotation_quality import assess_annotation_quality, validate_quality_policy


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
    reviewer_id: str
    quality: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AnnotationReviewItem:
    submission: AnnotationSubmission
    external_ref: str
    input_data: dict[str, Any]
    instructions: str
    rubric: dict[str, Any]


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
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_independent_assignments (
                assignment_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                annotator_id TEXT NOT NULL,
                round_number INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT,
                quality_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, task_id, annotator_id),
                FOREIGN KEY(task_id) REFERENCES annotation_tasks(task_id),
                FOREIGN KEY(project_id) REFERENCES annotation_projects(project_id)
            )""")
            assignment_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(annotation_independent_assignments)")
            }
            if "round_number" not in assignment_columns:
                db.execute("ALTER TABLE annotation_independent_assignments ADD COLUMN round_number INTEGER NOT NULL DEFAULT 1")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_independent_worker
                ON annotation_independent_assignments(annotator_id, status, created_at)""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_independent_round
                ON annotation_independent_assignments(owner_id, project_id, task_id, round_number, status)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_adjudications (
                adjudication_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                final_label TEXT,
                notes TEXT NOT NULL,
                source_assignment_ids_json TEXT NOT NULL,
                agreement REAL NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(owner_id, project_id, task_id),
                FOREIGN KEY(project_id) REFERENCES annotation_projects(project_id),
                FOREIGN KEY(task_id) REFERENCES annotation_tasks(task_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_adjudications_delivery
                ON annotation_adjudications(owner_id, project_id, decision, created_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_adjudication_events (
                event_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
                task_id TEXT NOT NULL, round_number INTEGER NOT NULL, adjudication_id TEXT NOT NULL,
                event_type TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_adjudication_events_task
                ON annotation_adjudication_events(owner_id, project_id, task_id, round_number, created_at)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_annotation_adjudication_events_adjudication
                ON annotation_adjudication_events(adjudication_id)""")
            db.execute("""INSERT OR IGNORE INTO annotation_adjudication_events
                (event_id, owner_id, project_id, task_id, round_number, adjudication_id,
                 event_type, snapshot_json, created_at)
                SELECT a.adjudication_id, a.owner_id, a.project_id, a.task_id, 1, a.adjudication_id,
                       'adjudicated', json_object('reviewer_id', a.reviewer_id, 'decision', a.decision,
                       'final_label', a.final_label, 'notes', a.notes,
                       'source_assignment_ids_json', a.source_assignment_ids_json,
                       'agreement', a.agreement), a.created_at
                FROM annotation_adjudications a
                WHERE NOT EXISTS (SELECT 1 FROM annotation_adjudication_events e
                                  WHERE e.adjudication_id=a.adjudication_id)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_delivery_manifests (
                manifest_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
                version INTEGER NOT NULL, idempotency_key TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                sha256 TEXT NOT NULL, item_count INTEGER NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(owner_id, project_id, version),
                UNIQUE(owner_id, project_id, idempotency_key)
            )""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS annotation_adjudication_events_no_update
                BEFORE UPDATE ON annotation_adjudication_events
                BEGIN SELECT RAISE(ABORT, 'annotation_adjudication_events_are_immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS annotation_adjudication_events_no_delete
                BEFORE DELETE ON annotation_adjudication_events
                BEGIN SELECT RAISE(ABORT, 'annotation_adjudication_events_are_immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS annotation_delivery_manifests_no_update
                BEFORE UPDATE ON annotation_delivery_manifests
                BEGIN SELECT RAISE(ABORT, 'annotation_delivery_manifests_are_immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS annotation_delivery_manifests_no_delete
                BEFORE DELETE ON annotation_delivery_manifests
                BEGIN SELECT RAISE(ABORT, 'annotation_delivery_manifests_are_immutable'); END""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_manifests_owner
                ON annotation_delivery_manifests(owner_id, project_id, created_at DESC)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_gold_standards (
                gold_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                expected_label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(owner_id, project_id, task_id),
                FOREIGN KEY(project_id) REFERENCES annotation_projects(project_id),
                FOREIGN KEY(task_id) REFERENCES annotation_tasks(task_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_gold_owner_project
                ON annotation_gold_standards(owner_id, project_id, created_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_gold_results (
                assignment_id TEXT PRIMARY KEY,
                gold_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                annotator_id TEXT NOT NULL,
                correct INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(gold_id) REFERENCES annotation_gold_standards(gold_id),
                FOREIGN KEY(assignment_id) REFERENCES annotation_independent_assignments(assignment_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_gold_results_owner
                ON annotation_gold_results(owner_id, project_id, annotator_id, created_at)""")
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
                reviewer_id TEXT NOT NULL DEFAULT '',
                quality_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES annotation_tasks(task_id)
            )""")
            submission_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(annotation_submissions)")
            }
            if "reviewer_id" not in submission_columns:
                db.execute("ALTER TABLE annotation_submissions ADD COLUMN reviewer_id TEXT NOT NULL DEFAULT ''")
            if "quality_json" not in submission_columns:
                db.execute("ALTER TABLE annotation_submissions ADD COLUMN quality_json TEXT NOT NULL DEFAULT '{}'")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_submissions_task
                ON annotation_submissions(owner_id, task_id, created_at DESC)""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_submissions_review_queue
                ON annotation_submissions(owner_id, project_id, status, created_at)""")
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
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_project_reviewers (
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                revoked_at TEXT,
                PRIMARY KEY(owner_id, project_id, reviewer_id),
                FOREIGN KEY(project_id) REFERENCES annotation_projects(project_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_reviewers_active
                ON annotation_project_reviewers(reviewer_id, project_id, revoked_at)""")
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
        value["quality"] = _decode(value.pop("quality_json", "{}"))
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
        validate_quality_policy(rubric)
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
                          s.payload_json, s.ai_assisted, s.quality_json,
                          s.updated_at AS accepted_at
                   FROM annotation_tasks AS t
                   JOIN annotation_submissions AS s
                     ON s.task_id=t.task_id AND s.owner_id=t.owner_id
                   WHERE t.owner_id=? AND t.project_id=? AND s.status='accepted'
                   ORDER BY s.updated_at, s.submission_id LIMIT ?""",
                (owner_id, project_id, limit),
            ).fetchall()
        result = [{
            "external_ref": row["external_ref"],
            "asset_ref": row["asset_ref"],
            "input_data": _decode(row["input_json"]),
            "annotation": _decode(row["payload_json"]),
            "quality": _decode(row["quality_json"]),
            "ai_assisted": bool(row["ai_assisted"]),
            "accepted_at": row["accepted_at"],
        } for row in rows]
        remaining = max(0, limit - len(result))
        if remaining:
            with self._connect() as db:
                adjudicated = db.execute(
                    """SELECT t.external_ref, t.asset_ref, t.input_json,
                              a.final_label, a.agreement, a.reviewer_id,
                              a.source_assignment_ids_json, a.created_at
                       FROM annotation_adjudications a
                       JOIN annotation_tasks t
                         ON t.task_id=a.task_id AND t.owner_id=a.owner_id
                       WHERE a.owner_id=? AND a.project_id=? AND a.decision='accepted'
                       ORDER BY a.created_at, a.adjudication_id LIMIT ?""",
                    (owner_id, project_id, remaining),
                ).fetchall()
            result.extend({
                "external_ref": row["external_ref"],
                "asset_ref": row["asset_ref"],
                "input_data": _decode(row["input_json"]),
                "annotation": {"label": row["final_label"]},
                "quality": {"source": "independent_adjudication",
                            "annotator_agreement": row["agreement"],
                            "reviewer_id": row["reviewer_id"],
                            "source_assignment_ids": json.loads(row["source_assignment_ids_json"])},
                "ai_assisted": False,
                "accepted_at": row["created_at"],
            } for row in adjudicated)
        return result

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
            rubric = _decode(project["rubric_json"])
            validate_annotation_payload(project["task_type"], payload, rubric)
            quality = assess_annotation_quality(project["task_type"], payload, rubric)
            quality_json = _json_object(quality, maximum=16384, field="quality_assessment")
            if ai_assisted and project["ai_assistance_mode"] != "permitted_with_disclosure":
                raise AnnotationPolicyError("ai_assistance_not_permitted_for_project")
            db.execute(
                """INSERT INTO annotation_submissions
                (submission_id, task_id, project_id, owner_id, payload_json,
                 ai_assisted, human_confirmed, status, reviewer_notes, quality_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 'submitted', '', ?, ?, ?)""",
                (submission_id, task_id, task["project_id"], owner_id,
                 payload_json, int(ai_assisted), quality_json, now, now),
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

    def grant_reviewer(self, owner_id: str, project_id: str, reviewer_id: str) -> bool:
        owner_id, reviewer_id = _owner(owner_id), _owner(reviewer_id)
        if reviewer_id == owner_id:
            raise ValueError("project_owner_cannot_be_reviewer")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute(
                "SELECT status FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if project is None:
                return False
            if project["status"] == "completed":
                raise ValueError("project_completed")
            db.execute(
                """INSERT INTO annotation_project_reviewers
                (owner_id, project_id, reviewer_id, granted_at, revoked_at)
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(owner_id, project_id, reviewer_id)
                DO UPDATE SET granted_at=excluded.granted_at, revoked_at=NULL""",
                (owner_id, project_id, reviewer_id, now),
            )
            self._audit(db, owner_id, project_id, None, "reviewer_granted", {
                "reviewer_id": reviewer_id,
            })
        return True

    def revoke_reviewer(self, owner_id: str, project_id: str, reviewer_id: str) -> bool:
        owner_id, reviewer_id = _owner(owner_id), _owner(reviewer_id)
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """UPDATE annotation_project_reviewers SET revoked_at=?
                WHERE owner_id=? AND project_id=? AND reviewer_id=? AND revoked_at IS NULL""",
                (now, owner_id, project_id, reviewer_id),
            )
            if cursor.rowcount:
                self._audit(db, owner_id, project_id, None, "reviewer_revoked", {
                    "reviewer_id": reviewer_id,
                })
        return cursor.rowcount == 1

    def list_reviewers(self, owner_id: str, project_id: str) -> list[dict[str, str]]:
        owner_id = _owner(owner_id)
        if self.get_project(owner_id, project_id) is None:
            return []
        with self._connect() as db:
            rows = db.execute(
                """SELECT reviewer_id, granted_at FROM annotation_project_reviewers
                WHERE owner_id=? AND project_id=? AND revoked_at IS NULL
                ORDER BY granted_at, reviewer_id""",
                (owner_id, project_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def reviewer_owner(self, reviewer_id: str, project_id: str) -> str | None:
        reviewer_id = _owner(reviewer_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            return None
        with self._connect() as db:
            row = db.execute(
                """SELECT r.owner_id FROM annotation_project_reviewers r
                JOIN annotation_projects p ON p.project_id=r.project_id AND p.owner_id=r.owner_id
                WHERE r.reviewer_id=? AND r.project_id=? AND r.revoked_at IS NULL""",
                (reviewer_id, project_id),
            ).fetchone()
        return row["owner_id"] if row else None

    def review(
        self, owner_id: str, submission_id: str, *,
        decision: str, notes: str = "", reviewer_id: str | None = None,
        expected_project_id: str | None = None,
        require_reviewer_grant: bool = False,
    ) -> AnnotationSubmission | None:
        owner_id = _owner(owner_id)
        reviewer_id = _owner(reviewer_id) if reviewer_id is not None else ""
        if reviewer_id and reviewer_id == owner_id:
            raise PermissionError("self_review_prohibited")
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
            if expected_project_id is not None and row["project_id"] != expected_project_id:
                return None
            if require_reviewer_grant:
                grant = db.execute(
                    """SELECT 1 FROM annotation_project_reviewers
                    WHERE owner_id=? AND project_id=? AND reviewer_id=? AND revoked_at IS NULL""",
                    (owner_id, row["project_id"], reviewer_id),
                ).fetchone()
                if grant is None:
                    raise PermissionError("reviewer_access_revoked")
            if row["status"] != "submitted":
                raise ValueError("submission_already_reviewed")
            updated_submission = db.execute(
                "UPDATE annotation_submissions SET status=?, reviewer_notes=?, reviewer_id=?, updated_at=? "
                "WHERE owner_id=? AND submission_id=? AND status='submitted'",
                (decision, notes, reviewer_id, now, owner_id, submission_id),
            )
            if updated_submission.rowcount != 1:
                raise AnnotationConflict("submission_review_race")
            updated_task = db.execute(
                "UPDATE annotation_tasks SET status=?, version=version+1, updated_at=? "
                "WHERE owner_id=? AND task_id=? AND status='submitted'",
                (decision, now, owner_id, row["task_id"]),
            )
            if updated_task.rowcount != 1:
                raise AnnotationConflict("task_review_state_conflict")
            self._audit(db, owner_id, row["project_id"], row["task_id"],
                        "submission_reviewed", {"decision": decision, "reviewer_id": reviewer_id})
            updated = db.execute(
                "SELECT * FROM annotation_submissions WHERE owner_id=? AND submission_id=?",
                (owner_id, submission_id),
            ).fetchone()
        return self._submission(updated)

    def list_review_queue(
        self, owner_id: str, project_id: str, *, limit: int = 100,
        reviewer_id: str | None = None,
    ) -> list[AnnotationReviewItem] | None:
        """Return review items with task and rubric context; caller authorizes access."""
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            raise ValueError("invalid_project_id")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        with self._connect() as db:
            if reviewer_id is not None:
                reviewer_id = _owner(reviewer_id)
                db.execute("BEGIN")
                grant = db.execute(
                    """SELECT 1 FROM annotation_project_reviewers
                    WHERE owner_id=? AND project_id=? AND reviewer_id=? AND revoked_at IS NULL""",
                    (owner_id, project_id, reviewer_id),
                ).fetchone()
                if grant is None:
                    return None
            rows = db.execute(
                """SELECT s.*, t.external_ref, t.input_json, p.instructions, p.rubric_json
                FROM annotation_submissions s
                JOIN annotation_tasks t ON t.task_id=s.task_id AND t.owner_id=s.owner_id
                JOIN annotation_projects p ON p.project_id=s.project_id AND p.owner_id=s.owner_id
                WHERE s.owner_id=? AND s.project_id=? AND s.status='submitted'
                ORDER BY s.created_at ASC, s.submission_id LIMIT ?""",
                (owner_id, project_id, limit),
            ).fetchall()
        items = []
        for row in rows:
            values = dict(row)
            submission = self._submission({
                key: values[key] for key in (
                    "submission_id", "task_id", "project_id", "owner_id", "payload_json",
                    "ai_assisted", "human_confirmed", "status", "reviewer_notes",
                    "reviewer_id", "quality_json", "created_at", "updated_at",
                )
            })
            items.append(AnnotationReviewItem(
                submission=submission,
                external_ref=values["external_ref"],
                input_data=_decode(values["input_json"]),
                instructions=values["instructions"],
                rubric=_decode(values["rubric_json"]),
            ))
        return items

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

    def create_gold_standard(
        self, owner_id: str, project_id: str, task_id: str, expected_label: str,
    ) -> dict[str, Any] | None:
        owner_id = _owner(owner_id)
        if (not isinstance(project_id, str) or not _ID.fullmatch(project_id)
                or not isinstance(task_id, str) or not _ID.fullmatch(task_id)
                or not isinstance(expected_label, str) or not expected_label.strip()
                or len(expected_label) > 256):
            raise ValueError("invalid_gold_standard")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute(
                "SELECT task_type, rubric_json, status FROM annotation_projects "
                "WHERE owner_id=? AND project_id=?", (owner_id, project_id),
            ).fetchone()
            if project is None:
                return None
            if project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            if project["task_type"] != "text_classification":
                raise ValueError("gold_standard_requires_text_classification")
            label = expected_label.strip()
            validate_annotation_payload(
                project["task_type"], {"label": label}, _decode(project["rubric_json"])
            )
            task = db.execute(
                "SELECT task_id, status FROM annotation_tasks WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if task is None:
                return None
            if task["status"] != "pending":
                raise ValueError("gold_standard_task_must_be_pending")
            existing_gold = db.execute(
                "SELECT 1 FROM annotation_gold_standards WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if existing_gold is not None:
                raise ValueError("gold_standard_already_registered")
            assigned = db.execute(
                "SELECT COUNT(*) FROM annotation_independent_assignments "
                "WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()[0]
            submitted = db.execute(
                "SELECT COUNT(*) FROM annotation_submissions "
                "WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()[0]
            if assigned or submitted:
                raise ValueError("gold_standard_must_precede_annotation")
            gold_id, now = uuid.uuid4().hex, _now()
            db.execute(
                """INSERT INTO annotation_gold_standards
                   (gold_id, owner_id, project_id, task_id, expected_label, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (gold_id, owner_id, project_id, task_id, label, now),
            )
            self._audit(db, owner_id, project_id, task_id, "gold_standard_created",
                        {"gold_id": gold_id})
        return {"gold_id": gold_id, "project_id": project_id, "task_id": task_id,
                "created_at": now}

    def gold_calibration_report(
        self, owner_id: str, project_id: str, *, minimum_gold_tasks: int = 5,
        passing_accuracy: float = 0.8,
    ) -> dict[str, Any] | None:
        owner_id = _owner(owner_id)
        if (not isinstance(project_id, str) or not _ID.fullmatch(project_id)
                or type(minimum_gold_tasks) is not int or not 1 <= minimum_gold_tasks <= 100
                or type(passing_accuracy) not in (int, float)
                or not 0.5 <= passing_accuracy <= 1.0):
            raise ValueError("invalid_calibration_policy")
        with self._connect() as db:
            project = db.execute(
                "SELECT task_type FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if project is None:
                return None
            rows = db.execute(
                """SELECT annotator_id, COUNT(*) AS evaluated_count,
                          SUM(correct) AS correct_count, MAX(created_at) AS last_evaluated_at
                   FROM annotation_gold_results
                   WHERE owner_id=? AND project_id=?
                   GROUP BY annotator_id ORDER BY annotator_id""",
                (owner_id, project_id),
            ).fetchall()
            gold_count = db.execute(
                "SELECT COUNT(*) FROM annotation_gold_standards WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()[0]
        annotators = []
        for row in rows:
            count, correct = row["evaluated_count"], row["correct_count"]
            accuracy = correct / count if count else None
            status = ("calibrated" if count >= minimum_gold_tasks and accuracy >= passing_accuracy
                      else "needs_review" if count >= minimum_gold_tasks else "insufficient_evidence")
            annotators.append({
                "annotator_id": row["annotator_id"], "evaluated_gold_tasks": count,
                "correct_gold_tasks": correct, "accuracy": accuracy, "status": status,
                "last_evaluated_at": row["last_evaluated_at"],
            })
        return {
            "project_id": project_id, "supported": project["task_type"] == "text_classification",
            "gold_standard_task_count": gold_count,
            "minimum_gold_tasks": minimum_gold_tasks, "passing_accuracy": passing_accuracy,
            "annotators": annotators,
        }

    def create_independent_assignments(
        self, owner_id: str, project_id: str, task_id: str, annotator_ids: list[str],
    ) -> list[dict[str, Any]] | None:
        owner_id = _owner(owner_id)
        if (not isinstance(project_id, str) or not _ID.fullmatch(project_id)
                or not isinstance(task_id, str) or not _ID.fullmatch(task_id)):
            raise ValueError("invalid_assignment_target")
        if (not isinstance(annotator_ids, list) or not 2 <= len(annotator_ids) <= 20
                or any(not isinstance(x, str) or not x or len(x) > 256 for x in annotator_ids)
                or len(set(annotator_ids)) != len(annotator_ids)
                or owner_id in annotator_ids):
            raise ValueError("invalid_independent_annotators")
        now = _now()
        created = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute(
                "SELECT status FROM annotation_tasks WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if task is None:
                return None
            if task["status"] == "rework":
                raise ValueError("use_rework_assignment_endpoint")
            if task["status"] in {"accepted", "completed"}:
                raise ValueError("task_already_delivered")
            current_round = db.execute(
                "SELECT COALESCE(MAX(round_number), 0) FROM annotation_independent_assignments "
                "WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()[0] or 1
            project = db.execute(
                "SELECT task_type, rubric_json, status FROM annotation_projects "
                "WHERE owner_id=? AND project_id=?", (owner_id, project_id),
            ).fetchone()
            if project is None or project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            if project["task_type"] != "text_classification":
                raise ValueError("independent_agreement_requires_text_classification")
            for annotator_id in annotator_ids:
                assignment_id = uuid.uuid4().hex
                existing = db.execute(
                    "SELECT assignment_id, status FROM annotation_independent_assignments "
                    "WHERE owner_id=? AND task_id=? AND annotator_id=?",
                    (owner_id, task_id, annotator_id),
                ).fetchone()
                if existing is not None:
                    if existing["status"] != "revoked":
                        raise ValueError("annotator_already_assigned_to_task")
                    db.execute(
                        """UPDATE annotation_independent_assignments
                           SET assignment_id=?, round_number=?, status='assigned', version=1, payload_json=NULL,
                               quality_json='{}', created_at=?, updated_at=?
                           WHERE owner_id=? AND task_id=? AND annotator_id=? AND status='revoked'""",
                        (assignment_id, current_round, now, now, owner_id, task_id, annotator_id),
                    )
                else:
                    db.execute(
                        """INSERT INTO annotation_independent_assignments
                        (assignment_id, task_id, project_id, owner_id, annotator_id, round_number,
                         status, version, payload_json, quality_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'assigned', 1, NULL, '{}', ?, ?)""",
                        (assignment_id, task_id, project_id, owner_id, annotator_id,
                         current_round, now, now),
                    )
                created.append({"assignment_id": assignment_id, "task_id": task_id,
                    "project_id": project_id, "round_number": current_round, "status": "assigned", "version": 1,
                    "created_at": now})
            self._audit(db, owner_id, project_id, task_id, "independent_assignments_created",
                        {"assignment_count": len(created)})
        return created

    def create_rework_assignments(
        self, owner_id: str, project_id: str, task_id: str, annotator_ids: list[str],
    ) -> list[dict[str, Any]] | None:
        """Open a new immutable assignment round for a task adjudicated as rework."""
        owner_id = _owner(owner_id)
        if (not isinstance(project_id, str) or not _ID.fullmatch(project_id)
                or not isinstance(task_id, str) or not _ID.fullmatch(task_id)):
            raise ValueError("invalid_assignment_target")
        if (not isinstance(annotator_ids, list) or not 2 <= len(annotator_ids) <= 20
                or any(not isinstance(x, str) or not x or len(x) > 256 for x in annotator_ids)
                or len(set(annotator_ids)) != len(annotator_ids) or owner_id in annotator_ids):
            raise ValueError("invalid_independent_annotators")
        now = _now()
        created = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute(
                "SELECT status FROM annotation_tasks WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if task is None:
                return None
            project = db.execute(
                "SELECT task_type, status FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if project is None or project["status"] != "active" or project["task_type"] != "text_classification":
                raise ValueError("rework_requires_active_text_classification_project")
            if task["status"] != "rework":
                raise ValueError("task_not_marked_for_rework")
            current = db.execute(
                "SELECT decision FROM annotation_adjudications WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if current is None or current["decision"] != "rework":
                raise ValueError("rework_adjudication_required")
            prior_round = db.execute(
                "SELECT COALESCE(MAX(round_number), 0) FROM annotation_independent_assignments "
                "WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()[0]
            if prior_round < 1:
                raise ValueError("prior_assignment_round_missing")
            round_number = prior_round + 1
            db.execute(
                "UPDATE annotation_independent_assignments SET status='revoked', version=version+1, updated_at=? "
                "WHERE owner_id=? AND project_id=? AND task_id=? AND round_number<? "
                "AND status IN ('assigned','in_progress')",
                (now, owner_id, project_id, task_id, round_number),
            )
            for annotator_id in annotator_ids:
                prior = db.execute(
                    "SELECT 1 FROM annotation_independent_assignments "
                    "WHERE owner_id=? AND project_id=? AND task_id=? AND annotator_id=?",
                    (owner_id, project_id, task_id, annotator_id),
                ).fetchone()
                if prior is not None:
                    raise ValueError("rework_requires_new_annotators")
                assignment_id = uuid.uuid4().hex
                db.execute(
                    """INSERT INTO annotation_independent_assignments
                    (assignment_id, task_id, project_id, owner_id, annotator_id, round_number,
                     status, version, payload_json, quality_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'assigned', 1, NULL, '{}', ?, ?)""",
                    (assignment_id, task_id, project_id, owner_id, annotator_id,
                     round_number, now, now),
                )
                created.append({"assignment_id": assignment_id, "task_id": task_id,
                    "project_id": project_id, "round_number": round_number,
                    "status": "assigned", "version": 1, "created_at": now})
            db.execute(
                "UPDATE annotation_tasks SET status='pending', version=version+1, updated_at=? "
                "WHERE owner_id=? AND project_id=? AND task_id=? AND status='rework'",
                (now, owner_id, project_id, task_id),
            )
            self._audit(db, owner_id, project_id, task_id, "independent_rework_round_created", {
                "round_number": round_number, "assignment_count": len(created),
            })
        return created

    def revoke_independent_assignment(
        self, owner_id: str, project_id: str, assignment_id: str,
    ) -> bool:
        owner_id = _owner(owner_id)
        if (not isinstance(project_id, str) or not _ID.fullmatch(project_id)
                or not isinstance(assignment_id, str) or not _ID.fullmatch(assignment_id)):
            raise ValueError("invalid_assignment_target")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT task_id, status FROM annotation_independent_assignments
                   WHERE owner_id=? AND project_id=? AND assignment_id=?""",
                (owner_id, project_id, assignment_id),
            ).fetchone()
            if row is None:
                return False
            if row["status"] == "submitted":
                raise ValueError("submitted_assignment_cannot_be_revoked")
            db.execute(
                """UPDATE annotation_independent_assignments
                   SET status='revoked', version=version+1, updated_at=?
                   WHERE owner_id=? AND project_id=? AND assignment_id=?""",
                (_now(), owner_id, project_id, assignment_id),
            )
            self._audit(db, owner_id, project_id, row["task_id"],
                        "independent_assignment_revoked", {"assignment_id": assignment_id})
        return True

    def list_my_independent_assignments(self, annotator_id: str) -> list[dict[str, Any]]:
        annotator_id = _owner(annotator_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT a.assignment_id, a.task_id, a.project_id, a.owner_id,
                          a.round_number, a.status, a.version, a.created_at, a.updated_at,
                          t.external_ref, t.input_json, p.task_type, p.instructions, p.rubric_json
                   FROM annotation_independent_assignments a
                   JOIN annotation_tasks t ON t.task_id=a.task_id AND t.owner_id=a.owner_id
                   JOIN annotation_projects p ON p.project_id=a.project_id AND p.owner_id=a.owner_id
                   WHERE a.annotator_id=? ORDER BY a.created_at, a.assignment_id""",
                (annotator_id,),
            ).fetchall()
        return [{
            "assignment_id": row["assignment_id"], "task_id": row["task_id"],
            "project_id": row["project_id"], "owner_id": row["owner_id"],
            "round_number": row["round_number"], "status": row["status"], "version": row["version"],
            "external_ref": row["external_ref"], "input_data": _decode(row["input_json"]),
            "task_type": row["task_type"], "instructions": row["instructions"],
            "rubric": _decode(row["rubric_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        } for row in rows]

    def claim_independent_assignment(
        self, annotator_id: str, assignment_id: str, *, expected_version: int,
    ) -> dict[str, Any] | None:
        annotator_id = _owner(annotator_id)
        if not isinstance(assignment_id, str) or not _ID.fullmatch(assignment_id):
            raise ValueError("invalid_assignment_id")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("invalid_expected_version")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM annotation_independent_assignments "
                "WHERE annotator_id=? AND assignment_id=?", (annotator_id, assignment_id),
            ).fetchone()
            if row is None:
                return None
            if row["version"] != expected_version:
                raise AnnotationConflict("annotation_assignment_version_conflict")
            if row["status"] not in {"assigned", "in_progress"}:
                raise ValueError("annotation_assignment_not_claimable")
            db.execute(
                "UPDATE annotation_independent_assignments SET status='in_progress', "
                "version=version+1, updated_at=? WHERE assignment_id=? AND annotator_id=? AND version=?",
                (_now(), assignment_id, annotator_id, expected_version),
            )
            updated = db.execute(
                "SELECT assignment_id, task_id, project_id, round_number, status, version "
                "FROM annotation_independent_assignments WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
        return dict(updated)

    def submit_independent_assignment(
        self, annotator_id: str, assignment_id: str, *, payload: Mapping[str, Any],
        expected_version: int, human_confirmed: bool,
    ) -> dict[str, Any] | None:
        annotator_id = _owner(annotator_id)
        if not isinstance(assignment_id, str) or not _ID.fullmatch(assignment_id):
            raise ValueError("invalid_assignment_id")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("invalid_expected_version")
        if type(human_confirmed) is not bool or not human_confirmed:
            raise PermissionError("human_confirmation_required_before_submission")
        payload_json = _json_object(payload, maximum=_MAX_JSON_BYTES, field="annotation_payload")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            assignment = db.execute(
                "SELECT * FROM annotation_independent_assignments "
                "WHERE annotator_id=? AND assignment_id=?", (annotator_id, assignment_id),
            ).fetchone()
            if assignment is None:
                return None
            if assignment["version"] != expected_version:
                raise AnnotationConflict("annotation_assignment_version_conflict")
            if assignment["status"] != "in_progress":
                raise ValueError("annotation_assignment_not_submittable")
            project = db.execute(
                "SELECT task_type, rubric_json, status FROM annotation_projects "
                "WHERE owner_id=? AND project_id=?",
                (assignment["owner_id"], assignment["project_id"]),
            ).fetchone()
            if project is None or project["status"] != "active":
                raise ValueError("annotation_project_not_active")
            rubric = _decode(project["rubric_json"])
            validate_annotation_payload(project["task_type"], payload, rubric)
            quality = assess_annotation_quality(project["task_type"], payload, rubric)
            quality_json = _json_object(quality, maximum=16384, field="quality_assessment")
            now = _now()
            gold = db.execute(
                """SELECT gold_id, expected_label FROM annotation_gold_standards
                   WHERE owner_id=? AND project_id=? AND task_id=?""",
                (assignment["owner_id"], assignment["project_id"], assignment["task_id"]),
            ).fetchone()
            if gold is not None:
                submitted_label = payload.get("label") if isinstance(payload, Mapping) else None
                db.execute(
                    """INSERT INTO annotation_gold_results
                       (assignment_id, gold_id, owner_id, project_id, task_id,
                        annotator_id, correct, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (assignment_id, gold["gold_id"], assignment["owner_id"],
                     assignment["project_id"], assignment["task_id"], annotator_id,
                     int(submitted_label == gold["expected_label"]), now),
                )
            db.execute(
                """UPDATE annotation_independent_assignments
                   SET status='submitted', payload_json=?, quality_json=?, version=version+1, updated_at=?
                   WHERE assignment_id=? AND annotator_id=? AND version=?""",
                (payload_json, quality_json, now, assignment_id, annotator_id, expected_version),
            )
            self._audit(db, assignment["owner_id"], assignment["project_id"],
                        assignment["task_id"], "independent_annotation_submitted",
                        {"assignment_id": assignment_id, "annotator_id": annotator_id})
            row = db.execute(
                "SELECT assignment_id, task_id, project_id, round_number, status, version, quality_json, updated_at "
                "FROM annotation_independent_assignments WHERE assignment_id=?", (assignment_id,),
            ).fetchone()
        result = dict(row)
        result["quality"] = _decode(result.pop("quality_json"))
        return result

    def list_independent_submissions(
        self, owner_id: str, project_id: str, task_id: str,
    ) -> list[dict[str, Any]] | None:
        owner_id = _owner(owner_id)
        if (not isinstance(project_id, str) or not _ID.fullmatch(project_id)
                or not isinstance(task_id, str) or not _ID.fullmatch(task_id)):
            raise ValueError("invalid_adjudication_target")
        with self._connect() as db:
            task = db.execute(
                "SELECT 1 FROM annotation_tasks WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if task is None:
                return None
            rows = db.execute(
                """SELECT assignment_id, annotator_id, round_number, payload_json, quality_json, updated_at
                   FROM annotation_independent_assignments
                   WHERE owner_id=? AND project_id=? AND task_id=? AND round_number=(
                       SELECT MAX(round_number) FROM annotation_independent_assignments
                       WHERE owner_id=? AND project_id=? AND task_id=?
                   ) AND status='submitted'
                   ORDER BY annotator_id, assignment_id""",
                (owner_id, project_id, task_id, owner_id, project_id, task_id),
            ).fetchall()
        return [{
            "assignment_id": row["assignment_id"], "annotator_id": row["annotator_id"],
            "round_number": row["round_number"], "payload": _decode(row["payload_json"]),
            "quality": _decode(row["quality_json"]), "submitted_at": row["updated_at"],
        } for row in rows]

    def adjudicate_independent_task(
        self, owner_id: str, project_id: str, task_id: str, reviewer_id: str, *,
        decision: str, final_label: str | None = None, notes: str = "",
        require_reviewer_grant: bool = True, minimum_gold_tasks: int = 5,
        passing_accuracy: float = 0.8,
    ) -> dict[str, Any] | None:
        owner_id = _owner(owner_id)
        reviewer_id = _owner(reviewer_id)
        if reviewer_id == owner_id:
            is_owner = True
        else:
            is_owner = False
        if (not isinstance(project_id, str) or not _ID.fullmatch(project_id)
                or not isinstance(task_id, str) or not _ID.fullmatch(task_id)):
            raise ValueError("invalid_adjudication_target")
        if decision not in {"accepted", "rework"}:
            raise ValueError("invalid_adjudication_decision")
        if not isinstance(notes, str) or len(notes) > 4000:
            raise ValueError("invalid_adjudication_notes")
        if type(minimum_gold_tasks) is not int or not 1 <= minimum_gold_tasks <= 100:
            raise ValueError("invalid_calibration_policy")
        if type(passing_accuracy) not in (int, float) or not 0.5 <= passing_accuracy <= 1.0:
            raise ValueError("invalid_calibration_policy")
        if decision == "accepted" and (not isinstance(final_label, str) or not final_label.strip()):
            raise ValueError("final_label_required")
        if decision == "rework" and final_label is not None:
            raise ValueError("final_label_not_allowed_for_rework")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute(
                "SELECT task_type, rubric_json, status FROM annotation_projects "
                "WHERE owner_id=? AND project_id=?", (owner_id, project_id),
            ).fetchone()
            if project is None:
                return None
            if project["status"] != "active" or project["task_type"] != "text_classification":
                raise ValueError("adjudication_requires_active_text_classification_project")
            task = db.execute(
                "SELECT external_ref, input_json, status FROM annotation_tasks "
                "WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if task is None:
                return None
            already_adjudicated = db.execute(
                "SELECT 1 FROM annotation_adjudications WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            if task["status"] in {"accepted", "completed"}:
                if already_adjudicated is not None:
                    raise ValueError("task_already_adjudicated")
                raise ValueError("task_already_delivered")
            if not is_owner:
                if require_reviewer_grant:
                    grant = db.execute(
                        "SELECT 1 FROM annotation_project_reviewers WHERE owner_id=? "
                        "AND project_id=? AND reviewer_id=? AND revoked_at IS NULL",
                        (owner_id, project_id, reviewer_id),
                    ).fetchone()
                    if grant is None:
                        raise PermissionError("reviewer_access_revoked")
                calibration = db.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(correct), 0) AS correct "
                    "FROM annotation_gold_results WHERE owner_id=? AND project_id=? AND annotator_id=?",
                    (owner_id, project_id, reviewer_id),
                ).fetchone()
                n = calibration["n"]
                accuracy = calibration["correct"] / n if n else 0.0
                if n < minimum_gold_tasks or accuracy < passing_accuracy:
                    raise PermissionError("reviewer_calibration_required")
            existing = db.execute(
                "SELECT * FROM annotation_adjudications WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()
            current_round = db.execute(
                "SELECT COALESCE(MAX(round_number), 0) FROM annotation_independent_assignments "
                "WHERE owner_id=? AND project_id=? AND task_id=?",
                (owner_id, project_id, task_id),
            ).fetchone()[0]
            if existing is not None:
                if existing["decision"] != "rework" or current_round <= 1:
                    raise ValueError("task_already_adjudicated")
                last_event_round = db.execute(
                    "SELECT MAX(round_number) FROM annotation_adjudication_events "
                    "WHERE owner_id=? AND project_id=? AND task_id=?",
                    (owner_id, project_id, task_id),
                ).fetchone()[0]
                if last_event_round is not None and current_round <= last_event_round:
                    raise ValueError("rework_assignment_round_required")
            rows = db.execute(
                "SELECT assignment_id, annotator_id, payload_json FROM annotation_independent_assignments "
                "WHERE owner_id=? AND project_id=? AND task_id=? AND round_number=? AND status='submitted' "
                "ORDER BY annotator_id, assignment_id",
                (owner_id, project_id, task_id, current_round),
            ).fetchall()
            if len(rows) < 2:
                raise ValueError("adjudication_requires_two_submissions")
            if not is_owner and any(row["annotator_id"] == reviewer_id for row in rows):
                raise PermissionError("self_review_prohibited")
            labels = []
            for row in rows:
                payload = _decode(row["payload_json"])
                label = payload.get("label")
                if not isinstance(label, str):
                    raise ValueError("submitted_classification_label_missing")
                labels.append(label)
            agreement = max(labels.count(label) for label in set(labels)) / len(labels)
            chosen_label = final_label.strip() if final_label is not None else None
            if decision == "accepted":
                validate_annotation_payload(
                    project["task_type"], {"label": chosen_label}, _decode(project["rubric_json"])
                )
            if (len(set(labels)) > 1
                    or (decision == "accepted" and chosen_label not in labels)) and not notes.strip():
                raise ValueError("disagreement_requires_adjudication_notes")
            adjudication_id = uuid.uuid4().hex
            source_ids = [row["assignment_id"] for row in rows]
            source_json = json.dumps(source_ids, separators=(",", ":"))
            event_snapshot = json.dumps({
                "reviewer_id": reviewer_id, "decision": decision, "final_label": chosen_label,
                "notes": notes, "source_assignment_ids": source_ids, "agreement": agreement,
                "round_number": current_round,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if existing is None:
                db.execute(
                    """INSERT INTO annotation_adjudications
                       (adjudication_id, owner_id, project_id, task_id, reviewer_id, decision,
                        final_label, notes, source_assignment_ids_json, agreement, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (adjudication_id, owner_id, project_id, task_id, reviewer_id, decision,
                     chosen_label, notes, source_json, agreement, now),
                )
            else:
                db.execute(
                    """UPDATE annotation_adjudications SET adjudication_id=?, reviewer_id=?,
                       decision=?, final_label=?, notes=?, source_assignment_ids_json=?,
                       agreement=?, created_at=? WHERE owner_id=? AND project_id=? AND task_id=?""",
                    (adjudication_id, reviewer_id, decision, chosen_label, notes, source_json,
                     agreement, now, owner_id, project_id, task_id),
                )
            db.execute(
                """INSERT INTO annotation_adjudication_events
                   (event_id, owner_id, project_id, task_id, round_number, adjudication_id,
                    event_type, snapshot_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'adjudicated', ?, ?)""",
                (uuid.uuid4().hex, owner_id, project_id, task_id, current_round,
                 adjudication_id, event_snapshot, now),
            )
            updated = db.execute(
                "UPDATE annotation_tasks SET status=?, version=version+1, updated_at=? "
                "WHERE owner_id=? AND project_id=? AND task_id=? AND status NOT IN ('accepted','completed')",
                (decision, now, owner_id, project_id, task_id),
            )
            if updated.rowcount != 1:
                raise AnnotationConflict("adjudication_task_state_conflict")
            self._audit(db, owner_id, project_id, task_id, "independent_task_adjudicated", {
                "adjudication_id": adjudication_id, "decision": decision,
                "reviewer_id": reviewer_id, "source_assignment_count": len(source_ids),
                "agreement": agreement,
            })
        return {
            "adjudication_id": adjudication_id, "project_id": project_id, "task_id": task_id,
            "decision": decision, "final_label": chosen_label, "reviewer_id": reviewer_id,
            "source_assignment_ids": source_ids, "submission_count": len(source_ids),
            "agreement": agreement, "round_number": current_round, "created_at": now,
        }

    def delivery_report(self, owner_id: str, project_id: str) -> dict[str, Any] | None:
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            return None
        with self._connect() as db:
            project = db.execute(
                "SELECT project_id FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if project is None:
                return None
            task_rows = db.execute(
                """SELECT t.task_id, t.status, a.decision
                   FROM annotation_tasks t
                   JOIN annotation_independent_assignments i
                     ON i.task_id=t.task_id AND i.owner_id=t.owner_id
                   LEFT JOIN annotation_adjudications a
                     ON a.task_id=t.task_id AND a.owner_id=t.owner_id
                   WHERE t.owner_id=? AND t.project_id=? AND i.project_id=?
                     AND NOT EXISTS (SELECT 1 FROM annotation_gold_standards g WHERE g.task_id=t.task_id AND g.owner_id=t.owner_id)
                   GROUP BY t.task_id, t.status, a.decision""",
                (owner_id, project_id, project_id),
            ).fetchall()
            task_count = db.execute(
                "SELECT COUNT(DISTINCT i.task_id) FROM annotation_independent_assignments i "
                "WHERE i.owner_id=? AND i.project_id=? AND NOT EXISTS "
                "(SELECT 1 FROM annotation_gold_standards g WHERE g.task_id=i.task_id AND g.owner_id=i.owner_id)",
                (owner_id, project_id),
            ).fetchone()[0]
            accepted_exports = db.execute(
                "SELECT COUNT(*) FROM annotation_submissions s WHERE owner_id=? AND project_id=? AND status='accepted' "
                "AND NOT EXISTS (SELECT 1 FROM annotation_gold_standards g WHERE g.task_id=s.task_id AND g.owner_id=s.owner_id)",
                (owner_id, project_id),
            ).fetchone()[0]
        accepted_independent = sum(1 for row in task_rows if row["status"] == "accepted" and row["decision"] == "accepted")
        rework_pending = sum(1 for row in task_rows if row["status"] == "rework")
        pending = sum(1 for row in task_rows if row["status"] in {"pending", "in_progress", "submitted"})
        adjudicated = accepted_independent + rework_pending
        return {
            "project_id": project_id, "task_count": task_count,
            "adjudicated_task_count": adjudicated,
            "pending_adjudication_count": pending,
            "accepted_independent_task_count": accepted_independent,
            "rework_independent_task_count": rework_pending,
            "accepted_single_submission_count": accepted_exports,
            "accepted_delivery_count": accepted_exports + accepted_independent,
        }


    def create_delivery_manifest(self, owner_id: str, project_id: str, *, idempotency_key: str) -> dict[str, Any] | None:
        """Create an immutable snapshot of accepted work with a canonical SHA-256 digest."""
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            return None
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValueError("invalid_manifest_idempotency_key")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute("SELECT 1 FROM annotation_projects WHERE owner_id=? AND project_id=?", (owner_id, project_id)).fetchone()
            if project is None:
                return None
            prior = db.execute("SELECT * FROM annotation_delivery_manifests WHERE owner_id=? AND project_id=? AND idempotency_key=?", (owner_id, project_id, idempotency_key)).fetchone()
            if prior is not None:
                return self._manifest_result(prior)
            ordinary = db.execute("""SELECT t.task_id, t.external_ref, t.asset_ref, t.input_json,
                s.submission_id, s.payload_json, s.ai_assisted, s.quality_json, s.updated_at AS accepted_at
                FROM annotation_tasks t JOIN annotation_submissions s ON s.task_id=t.task_id AND s.owner_id=t.owner_id
                WHERE t.owner_id=? AND t.project_id=? AND s.status='accepted'
                  AND NOT EXISTS (SELECT 1 FROM annotation_gold_standards g WHERE g.task_id=t.task_id AND g.owner_id=t.owner_id)
                ORDER BY s.updated_at, s.submission_id LIMIT 5001""", (owner_id, project_id)).fetchall()
            independent = db.execute("""SELECT t.task_id, t.external_ref, t.asset_ref, t.input_json,
                a.adjudication_id, a.final_label, a.agreement, a.reviewer_id,
                a.source_assignment_ids_json, a.created_at
                FROM annotation_adjudications a JOIN annotation_tasks t ON t.task_id=a.task_id AND t.owner_id=a.owner_id
                WHERE a.owner_id=? AND a.project_id=? AND a.decision='accepted'
                  AND NOT EXISTS (SELECT 1 FROM annotation_gold_standards g WHERE g.task_id=t.task_id AND g.owner_id=t.owner_id)
                ORDER BY a.created_at, a.adjudication_id LIMIT 5001""", (owner_id, project_id)).fetchall()
            if len(ordinary) + len(independent) > 5000:
                raise ValueError("manifest_item_limit_exceeded")
            items = [{
                "task_id": row["task_id"], "external_ref": row["external_ref"], "asset_ref": row["asset_ref"],
                "input_data": _decode(row["input_json"]), "annotation": _decode(row["payload_json"]),
                "quality": _decode(row["quality_json"]), "ai_assisted": bool(row["ai_assisted"]),
                "accepted_at": row["accepted_at"],
                "provenance": {"source": "accepted_submission", "submission_id": row["submission_id"]},
            } for row in ordinary]
            items.extend({
                "task_id": row["task_id"], "external_ref": row["external_ref"], "asset_ref": row["asset_ref"],
                "input_data": _decode(row["input_json"]), "annotation": {"label": row["final_label"]},
                "quality": {"source": "independent_adjudication", "annotator_agreement": row["agreement"],
                    "reviewer_id": row["reviewer_id"], "source_assignment_ids": json.loads(row["source_assignment_ids_json"])},
                "ai_assisted": False, "accepted_at": row["created_at"],
                "provenance": {"source": "independent_adjudication", "adjudication_id": row["adjudication_id"]},
            } for row in independent)
            task_totals = db.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN status NOT IN ('accepted','completed') "
                "THEN 1 ELSE 0 END) AS unresolved FROM annotation_tasks "
                "WHERE owner_id=? AND project_id=? AND NOT EXISTS "
                "(SELECT 1 FROM annotation_gold_standards g WHERE g.task_id=annotation_tasks.task_id AND g.owner_id=annotation_tasks.owner_id)",
                (owner_id, project_id),
            ).fetchone()
            if task_totals["unresolved"]:
                raise ValueError("manifest_has_unaccepted_tasks")
            if not items:
                raise ValueError("manifest_has_no_accepted_items")
            if len(items) != task_totals["total"]:
                raise ValueError("manifest_incomplete_task_coverage")
            task_ids = [item["task_id"] for item in items]
            if len(task_ids) != len(set(task_ids)):
                raise ValueError("manifest_contains_duplicate_tasks")
            version = db.execute("SELECT COALESCE(MAX(version), 0)+1 FROM annotation_delivery_manifests WHERE owner_id=? AND project_id=?", (owner_id, project_id)).fetchone()[0]
            manifest_id = uuid.uuid4().hex
            snapshot = json.dumps({
                "schema_version": 1, "manifest_id": manifest_id, "project_id": project_id,
                "version": version, "created_at": now, "item_count": len(items), "items": items,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            snapshot_bytes = snapshot.encode("utf-8")
            if len(snapshot_bytes) > 16 * 1024 * 1024:
                raise ValueError("manifest_size_limit_exceeded")
            digest = hashlib.sha256(snapshot_bytes).hexdigest()
            db.execute("""INSERT INTO annotation_delivery_manifests
                (manifest_id, owner_id, project_id, version, idempotency_key, snapshot_json, sha256, item_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (manifest_id, owner_id, project_id, version, idempotency_key, snapshot, digest, len(items), now))
            self._audit(db, owner_id, project_id, None, "delivery_manifest_created", {"manifest_id": manifest_id, "version": version, "item_count": len(items), "sha256": digest})
            row = db.execute("SELECT * FROM annotation_delivery_manifests WHERE manifest_id=?", (manifest_id,)).fetchone()
        return self._manifest_result(row)

    @staticmethod
    def _manifest_result(row: sqlite3.Row) -> dict[str, Any]:
        return {"manifest_id": row["manifest_id"], "project_id": row["project_id"], "version": row["version"],
            "sha256": row["sha256"], "item_count": row["item_count"], "created_at": row["created_at"]}

    def get_delivery_manifest(self, owner_id: str, project_id: str, manifest_id: str) -> dict[str, Any] | None:
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id) or not isinstance(manifest_id, str) or not _ID.fullmatch(manifest_id):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM annotation_delivery_manifests WHERE owner_id=? AND project_id=? AND manifest_id=?",
                (owner_id, project_id, manifest_id),
            ).fetchone()
        if row is None:
            return None
        result = self._manifest_result(row)
        result["snapshot"] = json.loads(row["snapshot_json"])
        return result

    def list_delivery_manifests(self, owner_id: str, project_id: str) -> list[dict[str, Any]] | None:
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            return None
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM annotation_projects WHERE owner_id=? AND project_id=?", (owner_id, project_id)).fetchone()
            if exists is None:
                return None
            rows = db.execute("SELECT * FROM annotation_delivery_manifests WHERE owner_id=? AND project_id=? ORDER BY version DESC", (owner_id, project_id)).fetchall()
        return [self._manifest_result(row) for row in rows]

    def verify_delivery_manifest(self, owner_id: str, project_id: str, manifest_id: str) -> dict[str, Any] | None:
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id) or not isinstance(manifest_id, str) or not _ID.fullmatch(manifest_id):
            return None
        with self._connect() as db:
            row = db.execute("SELECT * FROM annotation_delivery_manifests WHERE owner_id=? AND project_id=? AND manifest_id=?", (owner_id, project_id, manifest_id)).fetchone()
        if row is None:
            return None
        snapshot_json = row["snapshot_json"]
        actual = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        try:
            snapshot = json.loads(snapshot_json)
            metadata_valid = (
                snapshot.get("manifest_id") == row["manifest_id"]
                and snapshot.get("project_id") == row["project_id"]
                and snapshot.get("version") == row["version"]
                and snapshot.get("created_at") == row["created_at"]
                and snapshot.get("item_count") == row["item_count"]
                and isinstance(snapshot.get("items"), list)
                and len(snapshot["items"]) == row["item_count"]
            )
        except (TypeError, ValueError, AttributeError):
            metadata_valid = False
        return {
            "manifest_id": manifest_id, "valid": actual == row["sha256"] and metadata_valid,
            "metadata_valid": metadata_valid, "expected_sha256": row["sha256"],
            "actual_sha256": actual, "item_count": row["item_count"],
        }

    def independent_agreement_report(
        self, owner_id: str, project_id: str,
    ) -> dict[str, Any] | None:
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            return None
        with self._connect() as db:
            project = db.execute(
                "SELECT task_type FROM annotation_projects WHERE owner_id=? AND project_id=?",
                (owner_id, project_id),
            ).fetchone()
            if project is None:
                return None
            rows = db.execute(
                """SELECT task_id, annotator_id, status, payload_json
                   FROM annotation_independent_assignments
                   WHERE owner_id=? AND project_id=? ORDER BY task_id, annotator_id""",
                (owner_id, project_id),
            ).fetchall()
        if project["task_type"] != "text_classification":
            return {"project_id": project_id, "supported": False,
                    "reason": "text_classification_required", "task_count": 0,
                    "evaluated_task_count": 0, "mean_pairwise_agreement": None,
                    "fleiss_kappa": None}
        groups: dict[str, list[str]] = {}
        assignment_counts = {"assigned": 0, "in_progress": 0, "submitted": 0, "revoked": 0}
        assigned_tasks = {row["task_id"] for row in rows}
        for row in rows:
            if row["status"] in assignment_counts:
                assignment_counts[row["status"]] += 1
            if row["status"] != "submitted" or not row["payload_json"]:
                continue
            payload = _decode(row["payload_json"])
            label = payload.get("label") if isinstance(payload, dict) else None
            if isinstance(label, str):
                groups.setdefault(row["task_id"], []).append(label)
        agreements, fleiss_rows = [], []
        for labels in groups.values():
            if len(labels) < 2:
                continue
            n = len(labels)
            counts: dict[str, int] = {}
            for label in labels:
                counts[label] = counts.get(label, 0) + 1
            pair_total = n * (n - 1) / 2
            agreements.append(sum(v * (v - 1) / 2 for v in counts.values()) / pair_total)
            fleiss_rows.append((n, counts))
        if not fleiss_rows:
            kappa = None
        else:
            n_values = {n for n, _ in fleiss_rows}
            if len(n_values) != 1:
                kappa = None
            else:
                n = next(iter(n_values))
                categories = set().union(*(set(counts) for _, counts in fleiss_rows))
                p_i = [sum(v * v for v in counts.values()) - n for _, counts in fleiss_rows]
                p_i = [value / (n * (n - 1)) for value in p_i]
                total_ratings = n * len(fleiss_rows)
                p_j = {
                    category: sum(counts.get(category, 0) for _, counts in fleiss_rows) / total_ratings
                    for category in categories
                }
                p_bar = sum(p_i) / len(p_i)
                p_e = sum(value * value for value in p_j.values())
                kappa = (p_bar - p_e) / (1 - p_e) if p_e < 1 else None
        return {
            "project_id": project_id, "supported": True, "task_count": len(assigned_tasks),
            "assignment_counts": assignment_counts,
            "assignment_count": len(rows),
            "evaluated_task_count": len(agreements),
            "insufficient_annotators_task_count": sum(1 for labels in groups.values() if len(labels) < 2),
            "not_started_task_count": len(assigned_tasks - set(groups)),
            "mean_pairwise_agreement": sum(agreements) / len(agreements) if agreements else None,
            "fleiss_kappa": kappa, "metric": "classification_label_agreement",
            "minimum_ratings_per_task": 2,
        }

    def quality_report(self, owner_id: str, project_id: str) -> dict[str, Any] | None:
        """Aggregate deterministic quality findings without exposing annotation content."""
        owner_id = _owner(owner_id)
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            return None
        with self._connect() as db:
            project = db.execute(
                "SELECT project_id, task_type, rubric_json FROM annotation_projects "
                "WHERE owner_id=? AND project_id=?", (owner_id, project_id),
            ).fetchone()
            if project is None:
                return None
            rows = db.execute(
                "SELECT status, quality_json FROM annotation_submissions "
                "WHERE owner_id=? AND project_id=? ORDER BY created_at, submission_id",
                (owner_id, project_id),
            ).fetchall()
        finding_counts: dict[str, int] = {}
        quality_counts = {"pass": 0, "warning": 0, "unknown": 0}
        review_counts = {"submitted": 0, "accepted": 0, "rework": 0}
        total_items = 0
        for row in rows:
            review_status = row["status"]
            if review_status in review_counts:
                review_counts[review_status] += 1
            try:
                assessment = _decode(row["quality_json"])
            except (ValueError, TypeError):
                assessment = {}
            quality_status = assessment.get("status")
            if quality_status not in {"pass", "warning"}:
                quality_status = "unknown"
            quality_counts[quality_status] += 1
            metrics = assessment.get("metrics", {})
            if isinstance(metrics, dict) and type(metrics.get("annotation_items")) is int:
                total_items += metrics["annotation_items"]
            findings = assessment.get("findings", [])
            if isinstance(findings, list):
                for finding in findings:
                    if isinstance(finding, dict) and isinstance(finding.get("code"), str):
                        code = finding["code"]
                        finding_counts[code] = finding_counts.get(code, 0) + 1
        total = len(rows)
        return {
            "project_id": project_id,
            "task_type": project["task_type"],
            "submission_count": total,
            "quality": quality_counts,
            "quality_assessed_count": quality_counts["pass"] + quality_counts["warning"],
            "review": review_counts,
            "finding_counts": dict(sorted(finding_counts.items())),
            "annotation_items": total_items,
            "quality_pass_rate": (
                quality_counts["pass"] / (quality_counts["pass"] + quality_counts["warning"])
                if quality_counts["pass"] + quality_counts["warning"] else None
            ),
            "policy_version": 1,
        }

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
