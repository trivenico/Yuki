"""Durable, authorization-gated reviewer task ledger.

The ledger stores assignments, lifecycle transitions, and validated results.
It does not execute review work or turn a result into a verification report.
Every mutating/read operation requires a trusted authorization callback.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterable

from automation.reviewer_contracts import ReviewResult, ReviewTask
from automation.reviewer_signing import HMACReviewerSigner, ReviewerSignature

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_STATES = frozenset({"assigned", "in_progress", "submitted"})


class ReviewerLedgerError(RuntimeError):
    """Base reviewer-ledger error."""


class ReviewerLedgerConflict(ReviewerLedgerError):
    """Raised for stale transitions or conflicting idempotent submissions."""




def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _task_payload(task: ReviewTask) -> dict:
    return {
        "task_id": task.task_id,
        "artifact_sha256": task.artifact_sha256,
        "author_agent_id": task.author_agent_id,
        "reviewer_agent_id": task.reviewer_agent_id,
        "check_name": task.check_name,
        "requirements": list(task.requirements),
        "evidence_references": list(task.evidence_references),
    }


def _result_payload(result: ReviewResult) -> dict:
    return {
        "task_id": result.task_id,
        "artifact_sha256": result.artifact_sha256,
        "reviewer_agent_id": result.reviewer_agent_id,
        "check_name": result.check_name,
        "status": result.status,
        "summary": result.summary,
        "findings": [asdict(item) for item in result.findings],
    }


class ReviewerTaskLedger:
    """SQLite-backed reviewer assignment and result history.

    The authorizer signature is (actor_id, action, artifact_sha256) -> bool.
    It must resolve actor IDs to trusted principals and return literal True.
    """

    def __init__(self, database_path: str | Path, *,
                 authorize: Callable[[str, str, str], bool],
                 signer_resolver: Callable[[str, str | None], HMACReviewerSigner | None]):
        if not callable(authorize):
            raise TypeError("trusted_reviewer_authorizer_required")
        if not callable(signer_resolver):
            raise TypeError("trusted_reviewer_signer_resolver_required")
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path_required")
        self.authorize = authorize
        self.signer_resolver = signer_resolver
        Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS reviewer_tasks (
                    task_id TEXT PRIMARY KEY,
                    artifact_sha256 TEXT NOT NULL,
                    reviewer_agent_id TEXT NOT NULL,
                    task_json TEXT NOT NULL,
                    task_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('assigned','in_progress','submitted')),
                    result_json TEXT,
                    result_digest TEXT,
                    signature_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(reviewer_tasks)")}
            if "signature_json" not in columns:
                db.execute("ALTER TABLE reviewer_tasks ADD COLUMN signature_json TEXT")
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_reviewer_tasks_artifact
                ON reviewer_tasks(artifact_sha256, created_at)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS reviewer_task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES reviewer_tasks(task_id),
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, sequence)
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _actor(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("invalid_reviewer_actor_id")
        return value

    def _allowed(self, actor: str, action: str, digest: str) -> bool:
        try:
            return self.authorize(actor, action, digest) is True
        except Exception:
            return False

    def _event(self, db, task_id: str, sequence: int, event_type: str,
               actor: str, payload: dict, created_at: str) -> None:
        db.execute(
            """INSERT INTO reviewer_task_events
               (task_id,sequence,event_type,actor_id,payload_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (task_id, sequence, event_type, actor, _json(payload), created_at),
        )

    def create_tasks(self, *, actor_id: str, tasks: Iterable[ReviewTask]) -> tuple[dict, ...]:
        actor = self._actor(actor_id)
        values = tuple(tasks)
        if not values or len(values) > 100:
            raise ValueError("review_tasks_must_contain_1_to_100_items")
        ids = set()
        for task in values:
            if not isinstance(task, ReviewTask):
                raise TypeError("review_tasks_must_be_ReviewTask_values")
            task.validate()
            if task.task_id in ids:
                raise ValueError("duplicate_review_task_id")
            ids.add(task.task_id)
            if not self._allowed(actor, "review:assign", task.artifact_sha256):
                raise PermissionError("review task assignment is not authorized")
        now = _now()
        created = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for task in values:
                payload = _json(_task_payload(task))
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                row = db.execute(
                    "SELECT task_json,task_digest,state,version FROM reviewer_tasks WHERE task_id=?",
                    (task.task_id,),
                ).fetchone()
                if row is not None:
                    if row["task_json"] != payload or row["task_digest"] != digest:
                        raise ReviewerLedgerConflict("review_task_identity_conflict")
                    created.append({"task_id": task.task_id, "state": row["state"],
                                    "version": row["version"], "reused": True})
                    continue
                db.execute(
                    """INSERT INTO reviewer_tasks
                       (task_id,artifact_sha256,reviewer_agent_id,task_json,task_digest,
                        state,created_at,updated_at,version)
                       VALUES (?,?,?,?,?,'assigned',?,?,0)""",
                    (task.task_id, task.artifact_sha256, task.reviewer_agent_id,
                     payload, digest, now, now),
                )
                self._event(db, task.task_id, 0, "assigned", actor,
                            {"task_digest": digest}, now)
                created.append({"task_id": task.task_id, "state": "assigned",
                                "version": 0, "reused": False})
        return tuple(created)

    def claim(self, *, actor_id: str, task_id: str, expected_version: int) -> dict:
        actor = self._actor(actor_id)
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_review_task_id")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid_expected_version")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("review task not found")
            if actor != row["reviewer_agent_id"] or not self._allowed(actor, "review:execute", row["artifact_sha256"]):
                raise PermissionError("review task execution is not authorized")
            if row["version"] != expected_version or row["state"] != "assigned":
                raise ReviewerLedgerConflict("review_task_state_or_version_conflict")
            now = _now()
            version = expected_version + 1
            db.execute(
                "UPDATE reviewer_tasks SET state='in_progress',updated_at=?,version=? WHERE task_id=?",
                (now, version, task_id),
            )
            self._event(db, task_id, version, "claimed", actor, {}, now)
        return {"task_id": task_id, "state": "in_progress", "version": version}

    def submit(self, *, actor_id: str, task_id: str, expected_version: int,
               result: ReviewResult) -> dict:
        actor = self._actor(actor_id)
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_review_task_id")
        if not isinstance(result, ReviewResult):
            raise TypeError("review_result_required")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid_expected_version")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("review task not found")
            if actor != row["reviewer_agent_id"] or not self._allowed(actor, "review:submit", row["artifact_sha256"]):
                raise PermissionError("review result submission is not authorized")
            task_data = json.loads(row["task_json"])
            task = ReviewTask(
                task_id=task_data["task_id"],
                artifact_sha256=task_data["artifact_sha256"],
                author_agent_id=task_data["author_agent_id"],
                reviewer_agent_id=task_data["reviewer_agent_id"],
                check_name=task_data["check_name"],
                requirements=tuple(task_data["requirements"]),
                evidence_references=tuple(task_data["evidence_references"]),
            )
            result.validate(task)
            payload_data = _result_payload(result)
            payload = _json(payload_data)
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            signer = self.signer_resolver(actor, None)
            if not isinstance(signer, HMACReviewerSigner):
                raise ReviewerLedgerError("reviewer_signing_key_unavailable")
            signature = signer.sign(payload_data)
            signature_json = _json(asdict(signature))
            if row["state"] == "submitted":
                if row["result_json"] == payload and row["result_digest"] == digest:
                    return {"task_id": task_id, "state": "submitted",
                            "version": row["version"], "reused": True,
                            "result_digest": digest}
                raise ReviewerLedgerConflict("review_result_already_submitted")
            if row["state"] != "in_progress" or row["version"] != expected_version:
                raise ReviewerLedgerConflict("review_task_state_or_version_conflict")
            now = _now()
            version = expected_version + 1
            db.execute(
                """UPDATE reviewer_tasks SET state='submitted',result_json=?,
                   result_digest=?,signature_json=?,updated_at=?,version=? WHERE task_id=?""",
                (payload, digest, signature_json, now, version, task_id),
            )
            self._event(db, task_id, version, "submitted", actor,
                        {"result_digest": digest, "result_status": result.status}, now)
        return {"task_id": task_id, "state": "submitted", "version": version,
                "reused": False, "result_digest": digest}

    @staticmethod
    def _verify_integrity(row, events) -> None:
        """Verify canonical payload digests and the complete lifecycle sequence."""
        task_json = row["task_json"]
        task_digest = hashlib.sha256(task_json.encode("utf-8")).hexdigest()
        if task_digest != row["task_digest"]:
            raise ReviewerLedgerError("review_task_integrity_mismatch")
        try:
            task_data = json.loads(task_json)
            task = ReviewTask(
                task_id=task_data["task_id"],
                artifact_sha256=task_data["artifact_sha256"],
                author_agent_id=task_data["author_agent_id"],
                reviewer_agent_id=task_data["reviewer_agent_id"],
                check_name=task_data["check_name"],
                requirements=tuple(task_data["requirements"]),
                evidence_references=tuple(task_data["evidence_references"]),
            )
            task.validate()
        except Exception as exc:
            raise ReviewerLedgerError("review_task_payload_invalid") from exc
        if (task.task_id != row["task_id"] or task.artifact_sha256 != row["artifact_sha256"]
                or task.reviewer_agent_id != row["reviewer_agent_id"]):
            raise ReviewerLedgerError("review_task_row_binding_mismatch")

        expected = [("assigned", None)]
        if row["state"] in {"in_progress", "submitted"}:
            expected.append(("claimed", task.reviewer_agent_id))
        if row["state"] == "submitted":
            expected.append(("submitted", task.reviewer_agent_id))
        if row["state"] not in _STATES or row["version"] != len(expected) - 1:
            raise ReviewerLedgerError("review_task_state_version_mismatch")
        if len(events) != len(expected):
            raise ReviewerLedgerError("review_task_event_history_incomplete")

        result_digest = None
        if row["state"] == "submitted":
            if not row["result_json"] or not row["result_digest"] or not row["signature_json"]:
                raise ReviewerLedgerError("signed_review_result_missing")
            result_digest = hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest()
            if result_digest != row["result_digest"]:
                raise ReviewerLedgerError("review_result_integrity_mismatch")
            try:
                result_data = json.loads(row["result_json"])
                from automation.reviewer_contracts import ReviewFinding
                result = ReviewResult(
                    task_id=result_data["task_id"],
                    artifact_sha256=result_data["artifact_sha256"],
                    reviewer_agent_id=result_data["reviewer_agent_id"],
                    check_name=result_data["check_name"],
                    status=result_data["status"],
                    summary=result_data["summary"],
                    findings=tuple(ReviewFinding(**item) for item in result_data["findings"]),
                )
                result.validate(task)
                signature_data = json.loads(row["signature_json"])
                signature = ReviewerSignature(**signature_data)
                signer = self.signer_resolver(task.reviewer_agent_id, signature.key_id)
                if not isinstance(signer, HMACReviewerSigner) or not signer.verify(result_data, signature):
                    raise ReviewerLedgerError("review_result_signature_invalid")
            except ReviewerLedgerError:
                raise
            except Exception as exc:
                raise ReviewerLedgerError("review_result_payload_invalid") from exc
        elif row["result_json"] is not None or row["result_digest"] is not None or row["signature_json"] is not None:
            raise ReviewerLedgerError("unexpected_review_result")

        for index, (event, (event_type, expected_actor)) in enumerate(zip(events, expected)):
            if event["sequence"] != index or event["event_type"] != event_type:
                raise ReviewerLedgerError("review_event_sequence_mismatch")
            if not isinstance(event["actor_id"], str) or not _ID.fullmatch(event["actor_id"]):
                raise ReviewerLedgerError("review_event_actor_invalid")
            if expected_actor is not None and event["actor_id"] != expected_actor:
                raise ReviewerLedgerError("review_event_actor_mismatch")
            try:
                payload = json.loads(event["payload_json"])
            except (TypeError, ValueError) as exc:
                raise ReviewerLedgerError("review_event_payload_invalid") from exc
            if index == 0 and payload != {"task_digest": task_digest}:
                raise ReviewerLedgerError("review_assignment_event_mismatch")
            if index == 1 and payload != {}:
                raise ReviewerLedgerError("review_claim_event_mismatch")
            if index == 2 and payload != {
                "result_digest": result_digest,
                "result_status": json.loads(row["result_json"])["status"],
            }:
                raise ReviewerLedgerError("review_submission_event_mismatch")

    def get(self, *, actor_id: str, task_id: str) -> dict:
        actor = self._actor(actor_id)
        with self._connect() as db:
            row = db.execute("SELECT * FROM reviewer_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("review task not found")
            if not self._allowed(actor, "review:read", row["artifact_sha256"]):
                raise PermissionError("review task read is not authorized")
            events = db.execute(
                """SELECT sequence,event_type,actor_id,payload_json,created_at
                   FROM reviewer_task_events WHERE task_id=? ORDER BY sequence""",
                (task_id,),
            ).fetchall()
            self._verify_integrity(row, events)
        return {
            "task_id": row["task_id"],
            "artifact_sha256": row["artifact_sha256"],
            "reviewer_agent_id": row["reviewer_agent_id"],
            "task": json.loads(row["task_json"]),
            "task_digest": row["task_digest"],
            "state": row["state"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "result_digest": row["result_digest"],
            "signature": json.loads(row["signature_json"]) if row["signature_json"] else None,
            "signature_verified": row["state"] == "submitted",
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "events": tuple({
                "sequence": event["sequence"], "type": event["event_type"],
                "actor_id": event["actor_id"], "payload": json.loads(event["payload_json"]),
                "created_at": event["created_at"],
            } for event in events),
        }


__all__ = ["ReviewerTaskLedger", "ReviewerLedgerError", "ReviewerLedgerConflict"]
