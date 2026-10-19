"""Async durable job queue and cron-like daemon using SQLite and stdlib only."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any, Awaitable, Callable

class DurableTaskQueue:
    def __init__(self, database_path: str, *, max_attempts: int = 5):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20: raise ValueError("max_attempts must be 1..20")
        self.path = str(Path(database_path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs(
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                run_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, run_at)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def enqueue(self, task: str, payload: dict[str, Any], *, run_at: datetime | None = None) -> int:
        if not isinstance(task, str) or not task.replace("_", "").replace("-", "").isalnum() or len(task)>120: raise ValueError("Invalid task identifier")
        body = json.dumps(payload, allow_nan=False)
        if len(body.encode()) > 65536: raise ValueError("Payload too large")
        now = datetime.now(timezone.utc).isoformat(); scheduled = (run_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._connect() as db:
            cur = db.execute("INSERT INTO jobs(task,payload,status,attempts,run_at,created_at,updated_at) VALUES(?,?,'queued',0,?,?,?)",(task,body,scheduled,now,now))
            return int(cur.lastrowid)

    def claim(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,task,payload,attempts FROM jobs WHERE status IN ('queued','retry') AND run_at<=? ORDER BY run_at,id LIMIT 1",(now,)).fetchone()
            if not row: return None
            updated = db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated_at=? WHERE id=? AND status IN ('queued','retry')",(now,row[0]))
            if updated.rowcount != 1: return None
            return {"id":row[0],"task":row[1],"payload":json.loads(row[2]),"attempts":row[3]+1}

    def finish(self, job_id: int, *, error: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            row = db.execute("SELECT attempts FROM jobs WHERE id=? AND status='running'",(job_id,)).fetchone()
            if not row: return
            if error is None:
                db.execute("UPDATE jobs SET status='done',updated_at=?,last_error='' WHERE id=?",(now.isoformat(),job_id)); return
            attempts = row[0]
            if attempts >= self.max_attempts:
                status, next_run = "failed", now
            else:
                status, next_run = "retry", now.replace(microsecond=0)
                from datetime import timedelta
                next_run = next_run + timedelta(seconds=min(3600, 2 ** attempts * 5))
            db.execute("UPDATE jobs SET status=?,run_at=?,last_error=?,updated_at=? WHERE id=?",
                       (status,next_run.isoformat(),str(error)[:500],now.isoformat(),job_id))

class YukiDaemon:
    """Handlers are explicitly registered; persisted jobs never name import paths or shell commands."""
    def __init__(self, queue: DurableTaskQueue, *, poll_seconds: float = 1.0):
        if not 0.1 <= poll_seconds <= 60: raise ValueError("poll_seconds must be 0.1..60")
        self.queue, self.poll_seconds = queue, poll_seconds
        self.handlers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {}
        self._stop = asyncio.Event()

    def register(self, name: str, handler: Callable[[dict[str, Any]], Awaitable[Any]]) -> None:
        if name in self.handlers or not callable(handler): raise ValueError("Duplicate task or invalid handler")
        self.handlers[name] = handler

    async def run_once(self) -> bool:
        job = await asyncio.to_thread(self.queue.claim)
        if job is None: return False
        handler = self.handlers.get(job["task"])
        try:
            if handler is None: raise RuntimeError("Task handler is not registered")
            await handler(job["payload"])
        except Exception as exc:
            await asyncio.to_thread(self.queue.finish, job["id"], error=type(exc).__name__)
        else:
            await asyncio.to_thread(self.queue.finish, job["id"])
        return True

    async def serve(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try: await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError: pass

    def stop(self) -> None: self._stop.set()

class CronScheduler:
    """Minute-resolution UTC cron: '*' or comma-separated integers for each field."""
    def __init__(self, queue: DurableTaskQueue):
        self.queue, self.entries, self._seen = queue, [], set()

    def add(self, expression: str, task: str, payload: dict[str, Any]) -> None:
        parts = expression.split()
        if len(parts) != 5: raise ValueError("Cron expression must contain five fields")
        allowed = ((0,59),(0,23),(1,31),(1,12),(0,6))
        parsed=[]
        for token,(lo,hi) in zip(parts,allowed):
            if token=="*": parsed.append(None); continue
            values=set()
            for part in token.split(","):
                if not part.isdigit() or not lo<=int(part)<=hi: raise ValueError("Unsupported cron field")
                values.add(int(part))
            parsed.append(values)
        self.entries.append((parsed,task,dict(payload)))

    def tick(self, now: datetime | None = None) -> int:
        instant=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0,microsecond=0)
        key=instant.isoformat()
        if key in self._seen: return 0
        self._seen.add(key)
        if len(self._seen)>3000: self._seen.clear(); self._seen.add(key)
        fields=(instant.minute,instant.hour,instant.day,instant.month,(instant.weekday()+1)%7)
        added=0
        for parsed,task,payload in self.entries:
            if all(values is None or field in values for values,field in zip(parsed,fields)):
                self.queue.enqueue(task,payload,run_at=instant); added+=1
        return added
