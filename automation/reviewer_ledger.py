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
from automation.reviewer_sessions import ReviewerSessionAuthority
from automation.reviewer_leases import ReviewerLeaseAuthority

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
                 signer_resolver: Callable[[str, str | None], HMACReviewerSigner | None],
                 session_authority: ReviewerSessionAuthority,
                 lease_authority: ReviewerLeaseAuthority | None = None):
        if not callable(authorize):
            raise TypeError("trusted_reviewer_authorizer_required")
        if not callable(signer_resolver):
            raise TypeError("trusted_reviewer_signer_resolver_required")
        if not isinstance(session_authority, ReviewerSessionAuthority):
            raise TypeError("trusted_reviewer_session_authority_required")
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path_required")
        self.authorize = authorize
        self.signer_resolver = signer_resolver
        self.session_authority = session_authority
        if lease_authority is not None:
            if not isinstance(lease_authority, ReviewerLeaseAuthority):
                raise TypeError("trusted_reviewer_lease_authority_required")
            # Sharing one SQLite file lets BEGIN IMMEDIATE serialize ledger
            # transitions against lease renewals and recovery writes.
            if Path(lease_authority.database_path).resolve() != Path(self.database_path).resolve():
                raise ValueError("reviewer_lease_and_ledger_must_share_database")
        self.lease_authority = lease_authority
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
            db.execute("""
                CREATE TABLE IF NOT EXISTS reviewer_dispatch_outbox (
                    dispatch_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE REFERENCES reviewer_tasks(task_id),
                    artifact_sha256 TEXT NOT NULL,
                    reviewer_agent_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','claimed','delivered','dead_letter')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    claimed_by TEXT,
                    claim_token_digest TEXT,
                    claim_expires_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_reviewer_dispatch_ready
                ON reviewer_dispatch_outbox(state,available_at,created_at)
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

    def claim_dispatches(self, *, actor_id: str, worker_id: str, limit: int = 10,
                         claim_seconds: int = 60, max_attempts: int = 5) -> tuple[dict, ...]:
        """Claim bounded ready outbox records with expiring, opaque claim tokens."""
        actor=self._actor(actor_id)
        worker=self._actor(worker_id)
        if actor != worker:
            raise PermissionError("dispatch_worker_cannot_impersonate_another_actor")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_dispatch_claim_limit")
        if type(claim_seconds) is not int or not 10 <= claim_seconds <= 900:
            raise ValueError("invalid_dispatch_claim_ttl")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("invalid_dispatch_max_attempts")
        now_dt=datetime.now(timezone.utc)
        now=now_dt.isoformat()
        expiry=(now_dt + __import__("datetime").timedelta(seconds=claim_seconds)).isoformat()
        claimed=[]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            stale=db.execute("""SELECT dispatch_id,attempts FROM reviewer_dispatch_outbox
                WHERE state='claimed' AND claim_expires_at<=?""",(now,)).fetchall()
            for item in stale:
                state="dead_letter" if item["attempts"] >= max_attempts else "pending"
                db.execute("""UPDATE reviewer_dispatch_outbox SET state=?,claimed_by=NULL,
                    claim_token_digest=NULL,claim_expires_at=NULL,
                    available_at=?,last_error=?,updated_at=? WHERE dispatch_id=?""",
                    (state,now,"dispatch_claim_expired",now,item["dispatch_id"]))
            rows=db.execute("""SELECT * FROM reviewer_dispatch_outbox
                WHERE state='pending' AND available_at<=? AND reviewer_agent_id=?
                ORDER BY available_at,created_at,dispatch_id LIMIT ?""",(now,worker,limit)).fetchall()
            for row in rows:
                if not self._allowed(actor,"review:dispatch:claim",row["artifact_sha256"]):
                    continue
                raw=__import__("secrets").token_urlsafe(32)
                digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()
                attempts=row["attempts"]+1
                changed=db.execute("""UPDATE reviewer_dispatch_outbox SET state='claimed',
                    attempts=?,claimed_by=?,claim_token_digest=?,claim_expires_at=?,updated_at=?
                    WHERE dispatch_id=? AND state='pending'""",
                    (attempts,worker,digest,expiry,now,row["dispatch_id"])).rowcount
                if changed != 1:
                    continue
                claimed.append({
                    "dispatch_id":row["dispatch_id"],"task_id":row["task_id"],
                    "artifact_sha256":row["artifact_sha256"],
                    "reviewer_agent_id":row["reviewer_agent_id"],
                    "task":json.loads(db.execute(
                        "SELECT task_json FROM reviewer_tasks WHERE task_id=?",
                        (row["task_id"],)).fetchone()["task_json"]),
                    "attempts":attempts,"claim_token":raw,"claim_expires_at":expiry,
                })
        return tuple(claimed)

    def complete_dispatch(self, *, actor_id: str, dispatch_id: str,
                          claim_token: str) -> dict:
        """Mark a dispatch delivered only for its current unexpired claim."""
        actor=self._actor(actor_id)
        if not isinstance(dispatch_id,str) or not _ID.fullmatch(dispatch_id):
            raise ValueError("invalid_dispatch_id")
        if not isinstance(claim_token,str) or not 20 <= len(claim_token) <= 256:
            raise ValueError("invalid_dispatch_claim_token")
        token_digest=hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
        now=_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM reviewer_dispatch_outbox WHERE dispatch_id=?",
                           (dispatch_id,)).fetchone()
            if row is None:
                raise KeyError("review dispatch not found")
            if not self._allowed(actor,"review:dispatch:complete",row["artifact_sha256"]):
                raise PermissionError("review dispatch completion not authorized")
            if (row["state"]!="claimed" or row["claimed_by"]!=actor
                or row["claim_token_digest"]!=token_digest
                or not row["claim_expires_at"]
                or datetime.fromisoformat(row["claim_expires_at"])<=datetime.now(timezone.utc)):
                raise ReviewerLedgerConflict("dispatch_claim_invalid_or_expired")
            db.execute("""UPDATE reviewer_dispatch_outbox SET state='delivered',
                claimed_by=NULL,claim_token_digest=NULL,claim_expires_at=NULL,
                updated_at=? WHERE dispatch_id=?""",(now,dispatch_id))
            return {"dispatch_id":dispatch_id,"task_id":row["task_id"],"state":"delivered"}

    def fail_dispatch(self, *, actor_id: str, dispatch_id: str, claim_token: str,
                      error_code: str, max_attempts: int = 5,
                      base_backoff_seconds: int = 2) -> dict:
        """Release a claimed dispatch with bounded exponential retry or dead-letter."""
        actor=self._actor(actor_id)
        if not isinstance(dispatch_id,str) or not _ID.fullmatch(dispatch_id):
            raise ValueError("invalid_dispatch_id")
        if not isinstance(claim_token,str) or not 20 <= len(claim_token) <= 256:
            raise ValueError("invalid_dispatch_claim_token")
        if not isinstance(error_code,str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}",error_code):
            raise ValueError("invalid_dispatch_error_code")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("invalid_dispatch_max_attempts")
        if type(base_backoff_seconds) is not int or not 1 <= base_backoff_seconds <= 300:
            raise ValueError("invalid_dispatch_backoff")
        token_digest=hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
        now_dt=datetime.now(timezone.utc)
        now=now_dt.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM reviewer_dispatch_outbox WHERE dispatch_id=?",
                           (dispatch_id,)).fetchone()
            if row is None:
                raise KeyError("review dispatch not found")
            if not self._allowed(actor,"review:dispatch:fail",row["artifact_sha256"]):
                raise PermissionError("review dispatch failure not authorized")
            if (row["state"]!="claimed" or row["claimed_by"]!=actor
                or row["claim_token_digest"]!=token_digest
                or not row["claim_expires_at"]
                or datetime.fromisoformat(row["claim_expires_at"])<=now_dt):
                raise ReviewerLedgerConflict("dispatch_claim_invalid_or_expired")
            terminal=row["attempts"]>=max_attempts
            delay=min(base_backoff_seconds * (2 ** max(0,row["attempts"]-1)),3600)
            available=(now_dt + __import__("datetime").timedelta(seconds=delay)).isoformat()
            state="dead_letter" if terminal else "pending"
            db.execute("""UPDATE reviewer_dispatch_outbox SET state=?,available_at=?,
                claimed_by=NULL,claim_token_digest=NULL,claim_expires_at=NULL,
                last_error=?,updated_at=? WHERE dispatch_id=?""",
                (state,available,error_code,now,dispatch_id))
            return {"dispatch_id":dispatch_id,"task_id":row["task_id"],
                    "state":state,"attempts":row["attempts"],
                    "available_at":available,"last_error":error_code}

    def list_dispatches(self, *, actor_id: str, artifact_sha256: str,
                        limit: int = 100) -> tuple[dict, ...]:
        actor=self._actor(actor_id)
        if not isinstance(artifact_sha256,str) or not _SHA.fullmatch(artifact_sha256):
            raise ValueError("invalid_artifact_sha256")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_dispatch_list_limit")
        if not self._allowed(actor,"review:dispatch:read",artifact_sha256):
            raise PermissionError("review dispatch listing not authorized")
        with self._connect() as db:
            rows=db.execute("""SELECT dispatch_id,task_id,artifact_sha256,reviewer_agent_id,
                state,attempts,available_at,claimed_by,claim_expires_at,last_error,created_at,updated_at
                FROM reviewer_dispatch_outbox WHERE artifact_sha256=?
                ORDER BY created_at,dispatch_id LIMIT ?""",(artifact_sha256,limit)).fetchall()
        return tuple(dict(row) for row in rows)

    def create_tasks_with_leases(self, *, actor_id: str, tasks: Iterable[ReviewTask],
                                 lease_ttl_seconds: int = 300) -> tuple[dict, ...]:
        """Persist assigned tasks and their initial leases in one SQLite transaction."""
        actor = self._actor(actor_id)
        if self.lease_authority is None:
            raise ReviewerLedgerError("reviewer_lease_authority_required")
        if Path(self.lease_authority.database_path).resolve() != Path(self.database_path).resolve():
            raise ReviewerLedgerError("reviewer_lease_database_must_match_ledger")
        values = tuple(tasks)
        if not values or len(values) > 100:
            raise ValueError("review_tasks_must_contain_1_to_100_items")
        if type(lease_ttl_seconds) is not int or not 30 <= lease_ttl_seconds <= 3600:
            raise ValueError("invalid_reviewer_lease_ttl")
        ids=set()
        for task in values:
            if not isinstance(task,ReviewTask):
                raise TypeError("review_tasks_must_be_ReviewTask_values")
            task.validate()
            if task.task_id in ids:
                raise ValueError("duplicate_review_task_id")
            ids.add(task.task_id)
            if not self._allowed(actor,"review:assign",task.artifact_sha256):
                raise PermissionError("review_task_assignment_not_authorized")
        created=[]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for task in values:
                payload=_json(_task_payload(task))
                digest=hashlib.sha256(payload.encode("utf-8")).hexdigest()
                row=db.execute("SELECT task_json,task_digest,state,version,reviewer_agent_id FROM reviewer_tasks WHERE task_id=?",
                               (task.task_id,)).fetchone()
                if row is not None:
                    if row["task_json"] != payload or row["task_digest"] != digest:
                        raise ReviewerLedgerConflict("review_task_identity_conflict")
                    lease_row=db.execute("SELECT artifact_sha256,reviewer_agent_id,generation,expires_at,token_nonce FROM reviewer_leases WHERE task_id=?",
                                          (task.task_id,)).fetchone()
                    if not lease_row or lease_row[0] != task.artifact_sha256 or lease_row[1] != task.reviewer_agent_id:
                        raise ReviewerLedgerConflict("existing_review_task_lease_mismatch")
                    created.append({"task_id":task.task_id,"state":row["state"],
                                    "version":row["version"],"reused":True,
                                    "lease_generation":lease_row[2],"lease_expires_at":lease_row[3],
                                    "dispatch_id":db.execute(
                                        "SELECT dispatch_id FROM reviewer_dispatch_outbox WHERE task_id=?",
                                        (task.task_id,)).fetchone()["dispatch_id"]})
                    continue
                now=_now()
                db.execute("""INSERT INTO reviewer_tasks
                    (task_id,artifact_sha256,reviewer_agent_id,task_json,task_digest,state,created_at,updated_at,version)
                    VALUES(?,?,?,?,?,'assigned',?,?,0)""",
                    (task.task_id,task.artifact_sha256,task.reviewer_agent_id,payload,digest,now,now))
                self._event(db,task.task_id,0,"assigned",actor,{"task_digest":digest},now)
                lease=self.lease_authority.acquire_in_transaction(
                    db,actor_id=actor,task_id=task.task_id,
                    artifact_sha256=task.artifact_sha256,
                    reviewer_agent_id=task.reviewer_agent_id,
                    ttl_seconds=lease_ttl_seconds,
                )
                dispatch_id = "dispatch-" + hashlib.sha256(
                    (task.task_id + ":" + digest).encode("utf-8")
                ).hexdigest()[:32]
                db.execute("""INSERT INTO reviewer_dispatch_outbox
                    (dispatch_id,task_id,artifact_sha256,reviewer_agent_id,state,
                     attempts,available_at,created_at,updated_at)
                    VALUES(?,?,?,?,'pending',0,?,?,?)""",
                    (dispatch_id,task.task_id,task.artifact_sha256,
                     task.reviewer_agent_id,now,now,now))
                created.append({"task_id":task.task_id,"state":"assigned","version":0,
                                "reused":False,"lease_token":lease.token,
                                "lease_generation":lease.generation,
                                "lease_expires_at":lease.expires_at,
                                "dispatch_id":dispatch_id})
        return tuple(created)

    def abandon(self, *, actor_id: str, task_id: str, expected_version: int,
                reason: str) -> dict:
        """Mark a claimed task abandoned after its lease has expired."""
        actor = self._actor(actor_id)
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("invalid_expected_version")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("invalid_reviewer_abandonment_reason")
        if self.lease_authority is None:
            raise ReviewerLedgerError("reviewer_lease_authority_required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("review task not found")
            if not self._allowed(actor, "review:abandon", row["artifact_sha256"]):
                raise PermissionError("review task abandonment is not authorized")
            if row["state"] != "in_progress" or row["version"] != expected_version:
                raise ReviewerLedgerConflict("only_current_in_progress_tasks_can_be_abandoned")
            lease_row = db.execute("SELECT expires_at,reviewer_agent_id FROM reviewer_leases WHERE task_id=?",
                                   (task_id,)).fetchone()
            if (lease_row is None or lease_row["reviewer_agent_id"] != row["reviewer_agent_id"]
                    or datetime.fromisoformat(lease_row["expires_at"]) > datetime.now(timezone.utc)):
                raise ReviewerLedgerConflict("reviewer_lease_must_expire_before_abandonment")
            now = _now()
            version = expected_version + 1
            db.execute("UPDATE reviewer_tasks SET state='assigned',updated_at=?,version=? WHERE task_id=?",
                       (now,version,task_id))
            self._event(db,task_id,version,"abandoned",actor,
                        {"previous_reviewer_agent_id":row["reviewer_agent_id"],"reason":reason.strip()},now)
        return {"task_id":task_id,"state":"assigned","version":version,
                "reviewer_agent_id":row["reviewer_agent_id"]}

    def reassign(self, *, actor_id: str, task_id: str, expected_version: int,
                 replacement_reviewer_agent_id: str) -> dict:
        """Atomically reassign assigned or abandoned work and rotate its lease."""
        actor = self._actor(actor_id)
        replacement = self._actor(replacement_reviewer_agent_id)
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid_expected_version")
        if self.lease_authority is None:
            raise ReviewerLedgerError("reviewer_lease_authority_required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("review task not found")
            if not self._allowed(actor, "review:reassign", row["artifact_sha256"]):
                raise PermissionError("review task reassignment is not authorized")
            if row["state"] != "assigned" or row["version"] != expected_version:
                raise ReviewerLedgerConflict("only_current_unclaimed_tasks_can_be_reassigned")
            if replacement == row["reviewer_agent_id"]:
                raise ValueError("replacement_reviewer_must_be_different")
            lease = self.lease_authority.recover_in_transaction(
                db, actor_id=actor, task_id=task_id,
                artifact_sha256=row["artifact_sha256"],
                replacement_reviewer_agent_id=replacement,
            )
            now = _now()
            version = expected_version + 1
            previous = row["reviewer_agent_id"]
            db.execute("UPDATE reviewer_tasks SET reviewer_agent_id=?,updated_at=?,version=? WHERE task_id=?",
                       (replacement,now,version,task_id))
            self._event(db,task_id,version,"reassigned",actor,
                        {"previous_reviewer_agent_id":previous,
                         "replacement_reviewer_agent_id":replacement,
                         "lease_generation":lease.generation},now)
        return {"task_id":task_id,"state":"assigned","version":version,
                "reviewer_agent_id":replacement,"lease_generation":lease.generation,
                "lease_token":lease.token}

    def recover_in_progress(self, *, actor_id: str, task_id: str, expected_version: int,
                            replacement_reviewer_agent_id: str, reason: str) -> dict:
        """Atomically abandon expired work and reassign it to a new reviewer."""
        actor = self._actor(actor_id)
        replacement = self._actor(replacement_reviewer_agent_id)
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("invalid_expected_version")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("invalid_reviewer_recovery_reason")
        if self.lease_authority is None:
            raise ReviewerLedgerError("reviewer_lease_authority_required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("review task not found")
            if not self._allowed(actor,"review:abandon",row["artifact_sha256"]) or not self._allowed(actor,"review:reassign",row["artifact_sha256"]):
                raise PermissionError("review task recovery is not authorized")
            if row["state"] != "in_progress" or row["version"] != expected_version:
                raise ReviewerLedgerConflict("only_current_in_progress_tasks_can_be_recovered")
            if replacement == row["reviewer_agent_id"]:
                raise ValueError("replacement_reviewer_must_be_different")
            lease = self.lease_authority.recover_in_transaction(
                db,actor_id=actor,task_id=task_id,artifact_sha256=row["artifact_sha256"],
                replacement_reviewer_agent_id=replacement,
            )
            now = _now()
            abandoned_version = expected_version + 1
            reassigned_version = expected_version + 2
            previous = row["reviewer_agent_id"]
            db.execute("UPDATE reviewer_tasks SET state='assigned',reviewer_agent_id=?,updated_at=?,version=? WHERE task_id=?",
                       (replacement,now,reassigned_version,task_id))
            self._event(db,task_id,abandoned_version,"abandoned",actor,
                        {"previous_reviewer_agent_id":previous,"reason":reason.strip()},now)
            self._event(db,task_id,reassigned_version,"reassigned",actor,
                        {"previous_reviewer_agent_id":previous,
                         "replacement_reviewer_agent_id":replacement,
                         "lease_generation":lease.generation},now)
        return {"task_id":task_id,"state":"assigned","version":reassigned_version,
                "reviewer_agent_id":replacement,"lease_generation":lease.generation,
                "lease_token":lease.token}

    def claim(self, *, actor_id: str, task_id: str, expected_version: int,
              session_token: str, operation_id: str,
              lease_token: str | None = None, lease_generation: int | None = None) -> dict:
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
            if not self.session_authority.validate(session_token, reviewer_agent_id=actor, artifact_sha256=row["artifact_sha256"], action="review:execute", operation_id=operation_id):
                raise PermissionError("review execution session is invalid or replayed")
            if self.lease_authority is not None:
                if not self.lease_authority.validate(
                    lease_token, task_id=task_id, artifact_sha256=row["artifact_sha256"],
                    reviewer_agent_id=actor, generation=lease_generation,
                ):
                    raise PermissionError("review execution lease is invalid or superseded")
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
               result: ReviewResult, session_token: str, operation_id: str,
               lease_token: str | None = None, lease_generation: int | None = None) -> dict:
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
            if not self.session_authority.validate(session_token, reviewer_agent_id=actor, artifact_sha256=row["artifact_sha256"], action="review:submit", operation_id=operation_id):
                raise PermissionError("review submission session is invalid or replayed")
            if self.lease_authority is not None:
                if not self.lease_authority.validate(
                    lease_token, task_id=task_id, artifact_sha256=row["artifact_sha256"],
                    reviewer_agent_id=actor, generation=lease_generation,
                ):
                    raise PermissionError("review submission lease is invalid or superseded")
            task_data = json.loads(row["task_json"])
            task = ReviewTask(
                task_id=task_data["task_id"],
                artifact_sha256=task_data["artifact_sha256"],
                author_agent_id=task_data["author_agent_id"],
                reviewer_agent_id=row["reviewer_agent_id"],
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
        """Verify signed result integrity and replay the complete task lifecycle."""
        task_json = row["task_json"]
        task_digest = hashlib.sha256(task_json.encode("utf-8")).hexdigest()
        if task_digest != row["task_digest"]:
            raise ReviewerLedgerError("review_task_integrity_mismatch")
        try:
            task_data = json.loads(task_json)
            task = ReviewTask(
                task_id=task_data["task_id"], artifact_sha256=task_data["artifact_sha256"],
                author_agent_id=task_data["author_agent_id"],
                reviewer_agent_id=row["reviewer_agent_id"], check_name=task_data["check_name"],
                requirements=tuple(task_data["requirements"]),
                evidence_references=tuple(task_data["evidence_references"]),
            )
            task.validate()
        except Exception as exc:
            raise ReviewerLedgerError("review_task_payload_invalid") from exc
        if task.task_id != row["task_id"] or task.artifact_sha256 != row["artifact_sha256"]:
            raise ReviewerLedgerError("review_task_row_binding_mismatch")
        if row["state"] not in _STATES or not events or row["version"] != len(events)-1:
            raise ReviewerLedgerError("review_task_state_version_mismatch")

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
                    task_id=result_data["task_id"], artifact_sha256=result_data["artifact_sha256"],
                    reviewer_agent_id=result_data["reviewer_agent_id"], check_name=result_data["check_name"],
                    status=result_data["status"], summary=result_data["summary"],
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
        elif any(row[key] is not None for key in ("result_json","result_digest","signature_json")):
            raise ReviewerLedgerError("unexpected_review_result")

        current_reviewer = task_data["reviewer_agent_id"]
        lifecycle_state = "assigned"
        for index,event in enumerate(events):
            if event["sequence"] != index or not isinstance(event["actor_id"],str) or not _ID.fullmatch(event["actor_id"]):
                raise ReviewerLedgerError("review_event_sequence_or_actor_invalid")
            try:
                payload=json.loads(event["payload_json"])
            except (TypeError,ValueError) as exc:
                raise ReviewerLedgerError("review_event_payload_invalid") from exc
            kind=event["event_type"]
            if index == 0:
                if kind != "assigned" or payload != {"task_digest":task_digest}:
                    raise ReviewerLedgerError("review_assignment_event_mismatch")
                continue
            if kind == "claimed":
                if lifecycle_state != "assigned" or event["actor_id"] != current_reviewer or payload != {}:
                    raise ReviewerLedgerError("review_claim_event_mismatch")
                lifecycle_state="in_progress"
            elif kind == "abandoned":
                if (lifecycle_state != "in_progress" or
                    set(payload) != {"previous_reviewer_agent_id","reason"} or
                    payload["previous_reviewer_agent_id"] != current_reviewer or
                    not isinstance(payload["reason"],str) or not payload["reason"]):
                    raise ReviewerLedgerError("review_abandonment_event_mismatch")
                lifecycle_state="assigned"
            elif kind == "reassigned":
                if (lifecycle_state != "assigned" or
                    set(payload) != {"previous_reviewer_agent_id","replacement_reviewer_agent_id","lease_generation"} or
                    payload["previous_reviewer_agent_id"] != current_reviewer or
                    not isinstance(payload["replacement_reviewer_agent_id"],str) or
                    not _ID.fullmatch(payload["replacement_reviewer_agent_id"]) or
                    payload["replacement_reviewer_agent_id"] == current_reviewer or
                    type(payload["lease_generation"]) is not int or payload["lease_generation"] < 2):
                    raise ReviewerLedgerError("review_reassignment_event_mismatch")
                current_reviewer=payload["replacement_reviewer_agent_id"]
            elif kind == "submitted":
                if (lifecycle_state != "in_progress" or event["actor_id"] != current_reviewer or
                    payload != {"result_digest":result_digest,
                                "result_status":json.loads(row["result_json"])["status"]}):
                    raise ReviewerLedgerError("review_submission_event_mismatch")
                lifecycle_state="submitted"
            else:
                raise ReviewerLedgerError("unknown_review_lifecycle_event")
        if lifecycle_state != row["state"] or current_reviewer != row["reviewer_agent_id"]:
            raise ReviewerLedgerError("review_task_lifecycle_projection_mismatch")

    def list_expired_in_progress(self, *, actor_id: str, limit: int = 50) -> tuple[dict, ...]:
        """List expired in-progress tasks for an authorized recovery controller."""
        actor = self._actor(actor_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_reviewer_recovery_limit")
        now = _now()
        with self._connect() as db:
            rows = db.execute(
                """SELECT t.task_id,t.artifact_sha256,t.reviewer_agent_id,t.task_json,
                          t.version,l.expires_at,l.generation
                   FROM reviewer_tasks t JOIN reviewer_leases l ON l.task_id=t.task_id
                   WHERE t.state='in_progress' AND l.expires_at<=?
                   ORDER BY l.expires_at,t.task_id LIMIT ?""", (now,limit)
            ).fetchall()
            results=[]
            for row in rows:
                if not self._allowed(actor,"review:recover:list",row["artifact_sha256"]):
                    continue
                results.append({
                    "task_id":row["task_id"],"artifact_sha256":row["artifact_sha256"],
                    "reviewer_agent_id":row["reviewer_agent_id"],
                    "task":json.loads(row["task_json"]),"version":row["version"],
                    "lease_expires_at":row["expires_at"],"lease_generation":row["generation"],
                })
        return tuple(results)

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
            task_projection = json.loads(row["task_json"])
            task_projection["reviewer_agent_id"] = row["reviewer_agent_id"]
        return {
            "task_id": row["task_id"],
            "artifact_sha256": row["artifact_sha256"],
            "reviewer_agent_id": row["reviewer_agent_id"],
            "task": task_projection,
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
