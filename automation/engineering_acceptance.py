"""Durable engineering acceptance state machine.

Execution completion and engineering acceptance are separate states. This
store gates downstream release on an artifact with an accepted verification
report. It intentionally does not mutate coordinator task status yet.
"""
from __future__ import annotations

from datetime import datetime
import re
import sqlite3
from pathlib import Path
from typing import Callable

from automation.verification_workflow import VerificationWorkflow

_ID = re.compile(r"^[A-Za-z0-9_.:-][A-Za-z0-9_.:-]{0,127}$")


class EngineeringAcceptanceError(RuntimeError):
    pass


class EngineeringAcceptanceStore:
    STATES = frozenset({"awaiting_artifact", "awaiting_verification", "accepted", "rejected", "remediation"})

    def __init__(
        self,
        database_path: str | Path,
        verification_workflow: VerificationWorkflow,
        *,
        authorize: Callable[[str, str, str], bool],
    ):
        if not isinstance(verification_workflow, VerificationWorkflow):
            raise TypeError("verification_workflow must be a VerificationWorkflow")
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path is required")
        self.verification_workflow = verification_workflow
        self.authorize = authorize
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS engineering_acceptance (
                    plan_id TEXT NOT NULL,
                    subtask_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                      ('awaiting_artifact','awaiting_verification','accepted','rejected','remediation')),
                    artifact_id TEXT,
                    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(plan_id, subtask_id, task_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS engineering_acceptance_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    subtask_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    artifact_id TEXT,
                    created_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _id(value: str, field: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("invalid_" + field)
        return value

    @staticmethod
    def _time(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("timestamp_required")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid_timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp_must_be_timezone_aware")
        return value

    def register_task(self, *, plan_id: str, subtask_id: str, task_id: str,
                      owner_id: str, actor_id: str, created_at: str) -> dict:
        for value, name in ((plan_id, "plan_id"), (subtask_id, "subtask_id"),
                            (task_id, "task_id"), (owner_id, "owner_id"),
                            (actor_id, "actor_id")):
            self._id(value, name)
        created_at = self._time(created_at)
        if self.authorize(actor_id, "acceptance:register", plan_id) is not True:
            raise PermissionError("acceptance registration is not authorized")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM engineering_acceptance WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (plan_id, subtask_id, task_id),
            ).fetchone()
            if row is not None:
                if row["owner_id"] != owner_id:
                    raise EngineeringAcceptanceError("acceptance_task_identity_conflict")
                return dict(row)
            db.execute(
                """INSERT INTO engineering_acceptance
                   (plan_id,subtask_id,task_id,owner_id,state,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (plan_id, subtask_id, task_id, owner_id, "awaiting_artifact", created_at),
            )
            db.execute(
                """INSERT INTO engineering_acceptance_events
                   (plan_id,subtask_id,task_id,actor_id,event_type,from_state,to_state,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (plan_id,subtask_id,task_id,actor_id,"registered",None,"awaiting_artifact",created_at),
            )
        return self.get(plan_id=plan_id, subtask_id=subtask_id, task_id=task_id)

    def attach_artifact(self, *, plan_id: str, subtask_id: str, task_id: str,
                        artifact_id: str, actor_id: str, created_at: str) -> dict:
        for value, name in ((plan_id, "plan_id"), (subtask_id, "subtask_id"),
                            (task_id, "task_id"), (artifact_id, "artifact_id"),
                            (actor_id, "actor_id")):
            self._id(value, name)
        created_at = self._time(created_at)
        if self.authorize(actor_id, "acceptance:attach_artifact", plan_id) is not True:
            raise PermissionError("artifact attachment is not authorized")
        status = self.verification_workflow.get_status(artifact_id)
        artifact = status["artifact"]
        if (artifact["plan_id"], artifact["subtask_id"], artifact["task_id"]) != (plan_id, subtask_id, task_id):
            raise EngineeringAcceptanceError("artifact_task_identity_mismatch")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM engineering_acceptance WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (plan_id, subtask_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError("acceptance task not registered")
            if row["artifact_id"] == artifact_id and row["state"] in {"awaiting_verification", "accepted", "rejected"}:
                return dict(row)
            if row["state"] not in {"awaiting_artifact", "remediation", "rejected"}:
                raise EngineeringAcceptanceError("artifact_transition_not_allowed")
            revision = row["revision"] + 1
            db.execute(
                """UPDATE engineering_acceptance SET state='awaiting_verification',
                   artifact_id=?, revision=?, updated_at=?
                   WHERE plan_id=? AND subtask_id=? AND task_id=?""",
                (artifact_id, revision, created_at, plan_id, subtask_id, task_id),
            )
            db.execute(
                """INSERT INTO engineering_acceptance_events
                   (plan_id,subtask_id,task_id,actor_id,event_type,from_state,to_state,artifact_id,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (plan_id,subtask_id,task_id,actor_id,"artifact_attached",row["state"],
                 "awaiting_verification",artifact_id,created_at),
            )
        return self.get(plan_id=plan_id, subtask_id=subtask_id, task_id=task_id)

    def refresh_verification(self, *, plan_id: str, subtask_id: str, task_id: str,
                             actor_id: str, created_at: str) -> dict:
        for value, name in ((plan_id, "plan_id"), (subtask_id, "subtask_id"),
                            (task_id, "task_id"), (actor_id, "actor_id")):
            self._id(value, name)
        created_at = self._time(created_at)
        if self.authorize(actor_id, "acceptance:decide", plan_id) is not True:
            raise PermissionError("acceptance decision is not authorized")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM engineering_acceptance WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (plan_id, subtask_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError("acceptance task not registered")
        if row["artifact_id"] is None:
            raise EngineeringAcceptanceError("artifact_required_before_verification")
        status = self.verification_workflow.get_status(row["artifact_id"])
        target = (
            "accepted" if status["verified"]
            else "rejected" if status["reports"]
            else "awaiting_verification"
        )
        if target == row["state"]:
            return dict(row)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM engineering_acceptance WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (plan_id, subtask_id, task_id),
            ).fetchone()
            if current["artifact_id"] != row["artifact_id"]:
                raise EngineeringAcceptanceError("artifact_changed_during_verification")
            revision = current["revision"] + 1
            db.execute(
                "UPDATE engineering_acceptance SET state=?,revision=?,updated_at=? WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (target, revision, created_at, plan_id, subtask_id, task_id),
            )
            db.execute(
                """INSERT INTO engineering_acceptance_events
                   (plan_id,subtask_id,task_id,actor_id,event_type,from_state,to_state,artifact_id,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (plan_id,subtask_id,task_id,actor_id,"verification_refreshed",
                 current["state"],target,current["artifact_id"],created_at),
            )
        return self.get(plan_id=plan_id, subtask_id=subtask_id, task_id=task_id)

    def request_remediation(self, *, plan_id: str, subtask_id: str, task_id: str,
                            actor_id: str, created_at: str) -> dict:
        for value, name in ((plan_id, "plan_id"), (subtask_id, "subtask_id"),
                            (task_id, "task_id"), (actor_id, "actor_id")):
            self._id(value, name)
        created_at = self._time(created_at)
        if self.authorize(actor_id, "acceptance:remediate", plan_id) is not True:
            raise PermissionError("remediation is not authorized")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM engineering_acceptance WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (plan_id, subtask_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError("acceptance task not registered")
            if row["state"] not in {"awaiting_verification", "rejected"}:
                raise EngineeringAcceptanceError("remediation_transition_not_allowed")
            revision = row["revision"] + 1
            db.execute(
                "UPDATE engineering_acceptance SET state='remediation',revision=?,updated_at=? WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (revision,created_at,plan_id,subtask_id,task_id),
            )
            db.execute(
                """INSERT INTO engineering_acceptance_events
                   (plan_id,subtask_id,task_id,actor_id,event_type,from_state,to_state,artifact_id,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (plan_id,subtask_id,task_id,actor_id,"remediation_requested",
                 row["state"],"remediation",row["artifact_id"],created_at),
            )
        return self.get(plan_id=plan_id, subtask_id=subtask_id, task_id=task_id)

    def can_release_dependents(self, *, plan_id: str, subtask_id: str, task_id: str) -> bool:
        return self.get(plan_id=plan_id, subtask_id=subtask_id, task_id=task_id)["state"] == "accepted"

    def get(self, *, plan_id: str, subtask_id: str, task_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM engineering_acceptance WHERE plan_id=? AND subtask_id=? AND task_id=?",
                (plan_id, subtask_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError("acceptance task not registered")
            events = db.execute(
                """SELECT actor_id,event_type,from_state,to_state,artifact_id,created_at
                   FROM engineering_acceptance_events WHERE plan_id=? AND subtask_id=? AND task_id=?
                   ORDER BY event_id""", (plan_id,subtask_id,task_id)
            ).fetchall()
        return {**dict(row), "events": [dict(event) for event in events]}
