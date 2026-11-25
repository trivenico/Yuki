"""Durable, replay-safe cursors for security event source ingestion."""
from __future__ import annotations
import math, sqlite3, time, uuid
from core.sqlite_file_security import prepare_private_sqlite_path

class SecurityEventIngestionCursors:
    SOURCES=frozenset({"authorization_audit","workload_key_audit"})
    def __init__(self,database_path,*,clock=time.time,lease_seconds=60):
        if not callable(clock) or type(lease_seconds) is not int or not 5<=lease_seconds<=3600:
            raise ValueError("security_ingestion_cursor_configuration_invalid")
        self.database_path=prepare_private_sqlite_path(database_path); self.clock=clock; self.lease_seconds=lease_seconds
        with sqlite3.connect(self.database_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS security_ingestion_cursor(
                source TEXT PRIMARY KEY, event_id INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT, lease_token TEXT, lease_until REAL,
                last_success_at REAL, last_error_code TEXT)""")
    def _now(self):
        value=self.clock()
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError("security_ingestion_clock_invalid")
        return float(value)
    def claim(self,source,worker_id):
        if source not in self.SOURCES or not isinstance(worker_id,str) or not worker_id or len(worker_id)>96:
            raise ValueError("security_ingestion_claim_invalid")
        now=self._now(); token=uuid.uuid4().hex
        with sqlite3.connect(self.database_path,timeout=10,isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO security_ingestion_cursor(source) VALUES(?)",(source,))
            row=db.execute("SELECT event_id,lease_until FROM security_ingestion_cursor WHERE source=?",(source,)).fetchone()
            if row[1] is not None and float(row[1])>now:
                db.rollback(); return None
            db.execute("""UPDATE security_ingestion_cursor SET lease_owner=?,lease_token=?,lease_until=?
                WHERE source=?""",(worker_id,token,now+self.lease_seconds,source))
            db.commit()
        return {"source":source,"event_id":int(row[0]),"lease_token":token}
    def complete(self,claim,worker_id,event_id):
        if not isinstance(claim,dict) or claim.get("source") not in self.SOURCES or type(event_id) is not int or event_id<claim.get("event_id",-1):
            raise ValueError("security_ingestion_cursor_advance_invalid")
        now=self._now()
        with sqlite3.connect(self.database_path,timeout=10,isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT event_id,lease_owner,lease_token,lease_until FROM security_ingestion_cursor WHERE source=?",(claim["source"],)).fetchone()
            if row is None or row[1]!=worker_id or row[2]!=claim.get("lease_token") or row[3] is None or float(row[3])<=now:
                db.rollback(); raise PermissionError("security_ingestion_lease_lost")
            if event_id<int(row[0]):
                db.rollback(); raise ValueError("security_ingestion_cursor_regression")
            db.execute("""UPDATE security_ingestion_cursor SET event_id=?,last_success_at=?,last_error_code=NULL,
                lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE source=?""",(event_id,now,claim["source"]))
            db.commit()
        return event_id
    def fail(self,claim,worker_id,error_code="source_ingestion_failed"):
        if (not isinstance(claim,dict) or claim.get("source") not in self.SOURCES
                or not isinstance(worker_id,str) or not worker_id):
            raise ValueError("security_ingestion_failure_claim_invalid")
        if not isinstance(error_code,str) or not error_code.replace("_","").isalnum() or len(error_code)>64:
            raise ValueError("security_ingestion_error_code_invalid")
        now=self._now()
        with sqlite3.connect(self.database_path,timeout=10,isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT lease_owner,lease_token,lease_until FROM security_ingestion_cursor WHERE source=?",(claim["source"],)).fetchone()
            if (row is None or row[0]!=worker_id or row[1]!=claim.get("lease_token")
                    or row[2] is None or float(row[2])<=now):
                db.rollback(); raise PermissionError("security_ingestion_lease_lost")
            db.execute("""UPDATE security_ingestion_cursor SET last_error_code=?,lease_owner=NULL,
                lease_token=NULL,lease_until=NULL WHERE source=?""",(error_code,claim["source"]))
            db.commit()
    def snapshot(self):
        with sqlite3.connect(self.database_path) as db:
            rows=db.execute("SELECT source,event_id,last_success_at,last_error_code,lease_until FROM security_ingestion_cursor").fetchall()
        return {r[0]:{"event_id":int(r[1]),"last_success_at":r[2],"last_error_code":r[3],
            "lease_active":r[4] is not None and float(r[4])>self._now()} for r in rows}

class DurableSecurityEventIngestor:
    """Runs one bounded source page; cursor commits only after idempotent monitor import succeeds."""
    def __init__(self,monitor,cursors,*,key_registry=None,key_audit_operator=None,batch_size=500):
        if (not callable(getattr(monitor,"ingest_authorization_audit",None))
                or not all(callable(getattr(cursors, name, None)) for name in ("claim", "complete", "fail", "snapshot"))):
            raise TypeError("security_ingestion_dependencies_required")
        if type(batch_size) is not int or not 1<=batch_size<=5000: raise ValueError("security_ingestion_batch_size_invalid")
        if key_registry is not None and not callable(getattr(monitor,"ingest_key_lifecycle",None)):
            raise ValueError("security_key_audit_source_configuration_invalid")
        self.monitor,self.cursors,self.key_registry,self.key_audit_operator=monitor,cursors,key_registry,key_audit_operator
        self.batch_size=batch_size
    def ingest_authorization_page(self,audit_log,worker_id):
        return self._run("authorization_audit",worker_id,
            lambda cursor:self.monitor.ingest_authorization_audit(audit_log,after_event_id=cursor,limit=self.batch_size))
    def ingest_key_lifecycle_page(self,worker_id):
        if self.key_registry is None: raise RuntimeError("security_key_audit_source_not_configured")
        return self._run("workload_key_audit",worker_id,
            lambda cursor:self.monitor.ingest_key_lifecycle(
                self.key_registry, after_event_id=cursor, limit=self.batch_size,
                **({"administrator": self.key_audit_operator} if self.key_audit_operator is not None else {}),
            ))
    def _run(self,source,worker_id,operation):
        claim=self.cursors.claim(source,worker_id)
        if claim is None: return {"status":"busy","source":source}
        try:
            highest=operation(claim["event_id"])
            committed=self.cursors.complete(claim,worker_id,highest)
            return {"status":"ok","source":source,"cursor":committed}
        except Exception:
            try: self.cursors.fail(claim,worker_id)
            except PermissionError: pass
            raise
