"""Restart-safe SQLite task queue, leased workers, retries, and five-field UTC cron."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Awaitable, Callable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DurableTaskQueue:
    """At-least-once queue with expiring leases and optional enqueue idempotency keys.

    Handlers must be idempotent: a worker may finish an external side effect and crash
    before acknowledging it, in which case a later lease can cause the job to run again.
    """

    def __init__(self, database_path: str, *, max_attempts: int = 5, lease_seconds: int = 120):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be 1..20")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be 5..86400")
        self.path = str(Path(database_path).expanduser())
        parent = Path(self.path).parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        self.max_attempts, self.lease_seconds = max_attempts, lease_seconds
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs(
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                run_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                idempotency_key TEXT UNIQUE, lease_owner TEXT, lease_until TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, declaration in (
                ("idempotency_key", "TEXT"),
                ("lease_owner", "TEXT"),
                ("lease_until", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, run_at)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_lease ON jobs(status, lease_until)")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def enqueue(
        self,
        task: str,
        payload: dict[str, Any],
        *,
        run_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        if not isinstance(task, str) or not task or len(task) > 120 or not task.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Invalid task identifier")
        if not isinstance(payload, dict):
            raise ValueError("Task payload must be an object")
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"))
        if len(body.encode("utf-8")) > 65536:
            raise ValueError("Payload too large")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200
        ):
            raise ValueError("idempotency_key must be a non-empty string up to 200 characters")
        now = _utcnow()
        scheduled = run_at or now
        if scheduled.tzinfo is None or scheduled.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        run_text = scheduled.astimezone(timezone.utc).isoformat()
        now_text = now.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = db.execute("SELECT id FROM jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if existing:
                    db.execute("COMMIT")
                    return int(existing[0])
            cur = db.execute(
                """INSERT INTO jobs(task,payload,status,attempts,run_at,created_at,updated_at,idempotency_key)
                   VALUES(?,?,'queued',0,?,?,?,?)""",
                (task, body, run_text, now_text, now_text, idempotency_key),
            )
            job_id = int(cur.lastrowid)
            db.execute("COMMIT")
            return job_id

    def _recover_expired_leases(self, db: sqlite3.Connection, now: datetime) -> None:
        now_text = now.isoformat()
        # A crashed worker's lease is retried; attempts remain consumed to cap poison jobs.
        db.execute(
            """UPDATE jobs SET
                 status=CASE WHEN attempts>=? THEN 'failed' ELSE 'retry' END,
                 run_at=?, lease_owner=NULL, lease_until=NULL,
                 last_error='Worker lease expired', updated_at=?
               WHERE status='running' AND lease_until IS NOT NULL AND lease_until<=?""",
            (self.max_attempts, now_text, now_text, now_text),
        )

    def claim(self, worker_id: str | None = None) -> dict[str, Any] | None:
        owner = worker_id or secrets.token_urlsafe(18)
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
            raise ValueError("Invalid worker_id")
        now = _utcnow()
        until = (now + timedelta(seconds=self.lease_seconds)).isoformat()
        now_text = now.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover_expired_leases(db, now)
            row = db.execute(
                """SELECT id,task,payload,attempts FROM jobs
                   WHERE status IN ('queued','retry') AND run_at<=?
                   ORDER BY run_at,id LIMIT 1""",
                (now_text,),
            ).fetchone()
            if not row:
                db.execute("COMMIT")
                return None
            updated = db.execute(
                """UPDATE jobs SET status='running',attempts=attempts+1,updated_at=?,
                     lease_owner=?,lease_until=?
                   WHERE id=? AND status IN ('queued','retry')""",
                (now_text, owner, until, row[0]),
            )
            if updated.rowcount != 1:
                db.execute("ROLLBACK")
                return None
            db.execute("COMMIT")
            return {"id": row[0], "task": row[1], "payload": json.loads(row[2]),
                    "attempts": row[3] + 1, "worker_id": owner, "lease_until": until}

    def finish(self, job_id: int, *, worker_id: str | None = None, error: str | None = None) -> bool:
        now = _utcnow()
        now_text = now.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if worker_id is None:
                row = db.execute("SELECT attempts,lease_owner FROM jobs WHERE id=? AND status='running'", (job_id,)).fetchone()
            else:
                row = db.execute("SELECT attempts,lease_owner FROM jobs WHERE id=? AND status='running' AND lease_owner=?", (job_id, worker_id)).fetchone()
            if not row:
                db.execute("COMMIT")
                return False
            attempts = int(row[0])
            if error is None:
                db.execute(
                    "UPDATE jobs SET status='done',updated_at=?,last_error='',lease_owner=NULL,lease_until=NULL WHERE id=?",
                    (now_text, job_id),
                )
            else:
                safe_error = str(error)[:500]
                if attempts >= self.max_attempts:
                    status, next_run = "failed", now
                else:
                    status, next_run = "retry", now + timedelta(seconds=min(3600, 2 ** attempts * 5))
                db.execute(
                    """UPDATE jobs SET status=?,run_at=?,last_error=?,updated_at=?,lease_owner=NULL,lease_until=NULL
                       WHERE id=?""",
                    (status, next_run.isoformat(), safe_error, now_text, job_id),
                )
            db.execute("COMMIT")
            return True

    def recover(self) -> int:
        """Recover expired leases now; return the number of jobs whose status changed."""
        now = _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = db.total_changes
            self._recover_expired_leases(db, now)
            changed = db.total_changes - before
            db.execute("COMMIT")
            return changed


class YukiDaemon:
    """Only explicitly registered Python handlers can be dispatched from persisted jobs."""
    def __init__(self, queue: DurableTaskQueue, *, poll_seconds: float = 1.0, worker_id: str | None = None):
        if not isinstance(poll_seconds, (int, float)) or isinstance(poll_seconds, bool) or not 0.1 <= poll_seconds <= 60:
            raise ValueError("poll_seconds must be 0.1..60")
        self.queue, self.poll_seconds = queue, float(poll_seconds)
        self.worker_id = worker_id or secrets.token_urlsafe(18)
        self.handlers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {}
        self._stop = asyncio.Event()

    def register(self, name: str, handler: Callable[[dict[str, Any]], Awaitable[Any]]) -> None:
        if not isinstance(name, str) or not name or name in self.handlers or not callable(handler):
            raise ValueError("Duplicate task or invalid handler")
        self.handlers[name] = handler

    async def run_once(self) -> bool:
        job = await asyncio.to_thread(self.queue.claim, self.worker_id)
        if job is None:
            return False
        handler = self.handlers.get(job["task"])
        try:
            if handler is None:
                raise RuntimeError("Task handler is not registered")
            await handler(job["payload"])
        except Exception as exc:
            await asyncio.to_thread(self.queue.finish, job["id"], worker_id=self.worker_id, error=type(exc).__name__)
        else:
            await asyncio.to_thread(self.queue.finish, job["id"], worker_id=self.worker_id)
        return True

    async def serve(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()


class CronScheduler:
    """Five-field UTC cron with lists, ranges, and step values; durable minute dedupe."""
    def __init__(self, queue: DurableTaskQueue):
        self.queue, self.entries = queue, []

    @staticmethod
    def _parse_field(token: str, low: int, high: int) -> set[int] | None:
        if token == "*":
            return None
        values: set[int] = set()
        for component in token.split(","):
            if not component:
                raise ValueError("Empty cron list item")
            if "/" in component:
                base, step_text = component.split("/", 1)
                if not step_text.isdigit() or int(step_text) < 1:
                    raise ValueError("Cron step must be a positive integer")
                step = int(step_text)
                if base == "*":
                    start, end = low, high
                elif "-" in base:
                    parts = base.split("-", 1)
                    if not all(p.isdigit() for p in parts):
                        raise ValueError("Invalid cron range")
                    start, end = map(int, parts)
                elif base.isdigit():
                    start, end = int(base), high
                else:
                    raise ValueError("Invalid cron step base")
                if start < low or end > high or start > end:
                    raise ValueError("Cron field is out of range")
                values.update(range(start, end + 1, step))
            elif "-" in component:
                parts = component.split("-", 1)
                if not all(p.isdigit() for p in parts):
                    raise ValueError("Invalid cron range")
                start, end = map(int, parts)
                if start < low or end > high or start > end:
                    raise ValueError("Cron range is out of range")
                values.update(range(start, end + 1))
            elif component.isdigit() and low <= int(component) <= high:
                values.add(int(component))
            else:
                raise ValueError("Invalid cron field")
        if not values:
            raise ValueError("Cron field selects no values")
        return values

    def add(self, expression: str, task: str, payload: dict[str, Any]) -> None:
        if not isinstance(expression, str) or len(expression) > 200:
            raise ValueError("Invalid cron expression")
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError("Cron expression must contain five fields")
        bounds = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
        parsed = [self._parse_field(token, low, high) for token, (low, high) in zip(parts, bounds)]
        if not isinstance(payload, dict):
            raise ValueError("Cron payload must be an object")
        # Stable schedule key ensures a scheduler restart doesn't duplicate a minute's job.
        self.entries.append((expression, parsed, task, dict(payload)))

    def tick(self, now: datetime | None = None) -> int:
        instant = (now or _utcnow())
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Cron tick time must be timezone-aware")
        instant = instant.astimezone(timezone.utc).replace(second=0, microsecond=0)
        fields = (instant.minute, instant.hour, instant.day, instant.month, (instant.weekday() + 1) % 7)
        added = 0
        for index, (expression, parsed, task, payload) in enumerate(self.entries):
            if all(values is None or field in values for values, field in zip(parsed, fields)):
                key = f"cron:{hashlib_sha256(expression)}:{index}:{instant.isoformat()}"
                before = self.queue.enqueue(task, payload, run_at=instant, idempotency_key=key)
                # enqueue is idempotent; count is best-effort because the queue returns the existing ID too.
                added += 1
        return added


def hashlib_sha256(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
