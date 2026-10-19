"""Durable, approval-gated async scheduler for registered Yuki jobs.

Jobs are named handlers from the in-process allow-list, never shell commands or
model-generated code. Scheduling persists in SQLite; approval is checked on every run.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_JOBS = 1000
MAX_PAYLOAD_BYTES = 16_384
MAX_HANDLER_NAME = 80
Handler = Callable[[dict[str, Any]], Any | Awaitable[Any]]
Approver = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]


def _cron_field(value: str, minimum: int, maximum: int) -> frozenset[int]:
    """Parse a restricted five-field cron component (lists/ranges/steps/wildcards)."""
    output: set[int] = set()
    for part in value.split(","):
        if not part:
            raise ValueError("Empty cron component.")
        base, slash, step_text = part.partition("/")
        step = int(step_text) if slash else 1
        if step < 1 or step > maximum - minimum + 1:
            raise ValueError("Cron step is out of range.")
        if base == "*":
            low, high = minimum, maximum
        elif "-" in base:
            pieces = base.split("-", 1)
            low, high = int(pieces[0]), int(pieces[1])
        else:
            low = high = int(base)
        if low < minimum or high > maximum or low > high:
            raise ValueError("Cron value is out of range.")
        output.update(range(low, high + 1, step))
    return frozenset(output)


def _cron_matches(expression: str, timestamp: float) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron expressions must have exactly five fields.")
    minute, hour, day, month, weekday = (
        _cron_field(fields[0], 0, 59),
        _cron_field(fields[1], 0, 23),
        _cron_field(fields[2], 1, 31),
        _cron_field(fields[3], 1, 12),
        _cron_field(fields[4], 0, 6),
    )
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(timestamp, timezone.utc)
    # Cron weekday is Sunday=0; Python is Monday=0.
    return (dt.minute in minute and dt.hour in hour and dt.day in day
            and dt.month in month and ((dt.weekday() + 1) % 7) in weekday)


def _next_cron(expression: str, after: float) -> float:
    candidate = (int(after) // 60 + 1) * 60
    # Hard bound prevents malformed schedules from causing unbounded work.
    for _ in range(366 * 24 * 60 + 1):
        if _cron_matches(expression, candidate):
            return float(candidate)
        candidate += 60
    raise ValueError("Cron expression has no occurrence within one year.")


class AsyncJobScheduler:
    """Single-process SQLite scheduler. Register handlers before starting it."""

    def __init__(self, database: str | Path, *, approval_required: Approver,
                 poll_seconds: float = 1.0, max_jobs: int = MAX_JOBS):
        if not callable(approval_required):
            raise ValueError("A fail-closed approval callback is required.")
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)) or not 0.1 <= poll_seconds <= 60:
            raise ValueError("poll_seconds must be between 0.1 and 60.")
        if type(max_jobs) is not int or not 1 <= max_jobs <= MAX_JOBS:
            raise ValueError("max_jobs is out of range.")
        self.path = Path(database).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.approval_required = approval_required
        self.poll_seconds = float(poll_seconds)
        self.max_jobs = max_jobs
        self._handlers: dict[str, Handler] = {}
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _initialize(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY, handler TEXT NOT NULL, payload TEXT NOT NULL,
                schedule_type TEXT NOT NULL, schedule_value TEXT NOT NULL,
                next_run REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                last_status TEXT NOT NULL DEFAULT 'scheduled', last_error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, last_run REAL
            )""")

    def register_handler(self, name: str, handler: Handler) -> None:
        if not isinstance(name, str) or not name or len(name) > MAX_HANDLER_NAME or not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Handler name must be a simple bounded identifier.")
        if not callable(handler):
            raise TypeError("Handler must be callable.")
        if name in self._handlers:
            raise ValueError("Handler is already registered.")
        self._handlers[name] = handler

    def _insert(self, handler: str, payload: dict, schedule_type: str, schedule_value: str, next_run: float) -> str:
        if handler not in self._handlers:
            raise ValueError("Only explicitly registered job handlers may be scheduled.")
        if not isinstance(payload, dict):
            raise ValueError("Job payload must be a JSON object.")
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("Job payload exceeds the size limit.")
        job_id = uuid4().hex
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM scheduled_jobs WHERE enabled=1").fetchone()[0]
            if count >= self.max_jobs:
                raise ValueError("Scheduler capacity reached.")
            db.execute("INSERT INTO scheduled_jobs(id,handler,payload,schedule_type,schedule_value,next_run,created_at) VALUES(?,?,?,?,?,?,?)",
                       (job_id, handler, encoded, schedule_type, schedule_value, next_run, time.time()))
        return job_id

    def add_interval(self, handler: str, payload: dict, *, interval_seconds: int, run_immediately: bool = False) -> str:
        if type(interval_seconds) is not int or not 5 <= interval_seconds <= 365 * 24 * 3600:
            raise ValueError("interval_seconds must be between 5 seconds and one year.")
        now = time.time()
        return self._insert(handler, payload, "interval", str(interval_seconds), now if run_immediately else now + interval_seconds)

    def add_cron(self, handler: str, payload: dict, *, expression: str) -> str:
        if not isinstance(expression, str) or len(expression) > 100:
            raise ValueError("Cron expression is invalid.")
        next_run = _next_cron(expression, time.time())
        return self._insert(handler, payload, "cron", expression, next_run)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id,handler,payload,schedule_type,schedule_value,next_run,enabled,last_status,last_error,created_at,last_run FROM scheduled_jobs ORDER BY created_at DESC LIMIT ?",
                              (self.max_jobs,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            item["enabled"] = bool(item["enabled"])
            result.append(item)
        return result

    def cancel(self, job_id: str) -> bool:
        if not isinstance(job_id, str) or len(job_id) != 32:
            return False
        with self._connect() as db:
            cursor = db.execute("UPDATE scheduled_jobs SET enabled=0,last_status='cancelled' WHERE id=? AND enabled=1", (job_id,))
            return cursor.rowcount == 1

    def _claim_due(self, now: float) -> list[dict]:
        claimed = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM scheduled_jobs WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 50", (now,)).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                if row["schedule_type"] == "interval":
                    next_run = now + int(row["schedule_value"])
                elif row["schedule_type"] == "cron":
                    next_run = _next_cron(row["schedule_value"], now)
                else:
                    db.execute("UPDATE scheduled_jobs SET enabled=0,last_status='invalid_schedule' WHERE id=?", (row["id"],))
                    continue
                db.execute("UPDATE scheduled_jobs SET next_run=?,last_run=?,last_status='pending_approval',last_error='' WHERE id=?",
                           (next_run, now, row["id"]))
                claimed.append({"id": row["id"], "handler": row["handler"], "payload": payload})
        return claimed

    async def run_once(self, *, now: float | None = None) -> int:
        """Run one bounded due batch. Callback errors deny execution and are recorded."""
        async with self._lock:
            jobs = self._claim_due(time.time() if now is None else now)
            completed = 0
            for job in jobs:
                status, error = "denied", ""
                try:
                    approved = self.approval_required(job["handler"], dict(job["payload"]))
                    if hasattr(approved, "__await__"):
                        approved = await approved
                    if approved is not True:
                        status = "denied"
                    else:
                        handler = self._handlers.get(job["handler"])
                        if handler is None:
                            raise RuntimeError("Registered handler is no longer available.")
                        result = handler(dict(job["payload"]))
                        if hasattr(result, "__await__"):
                            await result
                        status = "succeeded"
                        completed += 1
                except Exception as exc:
                    status = "failed"
                    error = type(exc).__name__[:80]
                with self._connect() as db:
                    db.execute("UPDATE scheduled_jobs SET last_status=?,last_error=? WHERE id=?",
                               (status, error, job["id"]))
            return completed

    async def run_forever(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
