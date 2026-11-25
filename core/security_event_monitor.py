"""Durable, metadata-minimizing security signal detection and incident tracking."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from contextlib import closing
import hashlib, math, re, sqlite3, time
from typing import Callable
from core.sqlite_file_security import prepare_private_sqlite_path

_CATEGORIES=frozenset({"authorization_denied","admin_auth_denied","credential_verification_failed","key_revoked","audit_integrity_failure"})
_REASON=re.compile(r"^[a-zA-Z0-9_.:-]{1,96}$")
_HASH=re.compile(r"^[0-9a-f]{64}$")

@dataclass(frozen=True)
class SecuritySignal:
    signal_id: str
    category: str
    occurred_at: float
    reason_code: str
    subject_hash: str|None=None
    source: str="runtime"

class SecurityEventMonitor:
    """SQLite detector. Alerts only; never automatically contains or disables work."""
    def __init__(self,database_path:str,*,authorize_operator:Callable[[str],bool],failure_threshold:int=5,window_seconds:int=300,clock:Callable[[],float]=time.time):
        if not callable(authorize_operator) or not callable(clock): raise TypeError("security_monitor_dependencies_required")
        if type(failure_threshold) is not int or not 2<=failure_threshold<=1000: raise ValueError("security_failure_threshold_invalid")
        if type(window_seconds) is not int or not 1<=window_seconds<=86400: raise ValueError("security_window_invalid")
        self.database_path=prepare_private_sqlite_path(database_path); self.authorize_operator=authorize_operator
        self.failure_threshold=failure_threshold; self.window_seconds=window_seconds; self.clock=clock
        with closing(self._connect()) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS security_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,signal_id TEXT NOT NULL UNIQUE,category TEXT NOT NULL,occurred_at REAL NOT NULL,reason_code TEXT NOT NULL,subject_hash TEXT,source TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS security_events_window ON security_events(category,subject_hash,occurred_at);
            CREATE TABLE IF NOT EXISTS security_incidents(incident_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,category TEXT NOT NULL,severity TEXT NOT NULL CHECK(severity IN ('high','critical')),status TEXT NOT NULL CHECK(status IN ('open','acknowledged','resolved')),first_seen REAL NOT NULL,last_seen REAL NOT NULL,event_count INTEGER NOT NULL,last_reason_code TEXT NOT NULL,updated_by TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS security_incident_fingerprint ON security_incidents(fingerprint);
            CREATE TABLE IF NOT EXISTS security_incident_history(history_id INTEGER PRIMARY KEY AUTOINCREMENT,incident_id TEXT NOT NULL,action TEXT NOT NULL,operator_hash TEXT NOT NULL,occurred_at REAL NOT NULL);
            CREATE TRIGGER IF NOT EXISTS security_events_no_update BEFORE UPDATE ON security_events BEGIN SELECT RAISE(ABORT,'security_events_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS security_events_no_delete BEFORE DELETE ON security_events BEGIN SELECT RAISE(ABORT,'security_events_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS security_history_no_update BEFORE UPDATE ON security_incident_history BEGIN SELECT RAISE(ABORT,'security_history_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS security_history_no_delete BEFORE DELETE ON security_incident_history BEGIN SELECT RAISE(ABORT,'security_history_immutable'); END;
            """)
    def _connect(self):
        db=sqlite3.connect(self.database_path,timeout=10.0,isolation_level=None); db.row_factory=sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000"); return db
    def _now(self):
        v=self.clock()
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v): raise ValueError("security_monitor_clock_invalid")
        return float(v)
    @staticmethod
    def _validate(s):
        if not isinstance(s,SecuritySignal): raise TypeError("typed_security_signal_required")
        if not isinstance(s.signal_id,str) or not s.signal_id or len(s.signal_id)>128 or s.signal_id!=s.signal_id.strip(): raise ValueError("security_signal_id_invalid")
        if s.category not in _CATEGORIES: raise ValueError("security_signal_category_invalid")
        if isinstance(s.occurred_at,bool) or not isinstance(s.occurred_at,(int,float)) or not math.isfinite(s.occurred_at) or s.occurred_at<0: raise ValueError("security_signal_timestamp_invalid")
        if not isinstance(s.reason_code,str) or not _REASON.fullmatch(s.reason_code): raise ValueError("security_reason_code_invalid")
        if s.subject_hash is not None and (not isinstance(s.subject_hash,str) or not _HASH.fullmatch(s.subject_hash)): raise ValueError("security_subject_must_be_sha256_hash")
        if not isinstance(s.source,str) or not _REASON.fullmatch(s.source): raise ValueError("security_signal_source_invalid")
    def record(self,signal:SecuritySignal)->dict|None:
        self._validate(signal); self._now(); db=self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cur=db.execute("INSERT OR IGNORE INTO security_events(signal_id,category,occurred_at,reason_code,subject_hash,source) VALUES(?,?,?,?,?,?)",
                (signal.signal_id,signal.category,float(signal.occurred_at),signal.reason_code,signal.subject_hash,signal.source))
            if cur.rowcount==0: db.commit(); return None
            incident=None
            if signal.category in {"key_revoked","audit_integrity_failure"}:
                sev="critical" if signal.category=="audit_integrity_failure" else "high"
                incident=self._upsert(db,signal.category+":"+(signal.subject_hash or "global"),signal,sev,1)
            elif signal.category in {"authorization_denied","admin_auth_denied","credential_verification_failed"} and signal.subject_hash:
                count=db.execute("SELECT COUNT(*) FROM security_events WHERE category=? AND subject_hash=? AND occurred_at>=? AND occurred_at<=?",
                    (signal.category,signal.subject_hash,float(signal.occurred_at)-self.window_seconds,float(signal.occurred_at))).fetchone()[0]
                if count>=self.failure_threshold: incident=self._upsert(db,signal.category+":"+signal.subject_hash,signal,"high",count)
            db.commit(); return incident
        except Exception: db.rollback(); raise
        finally: db.close()
    def ingest_authorization_audit(self, audit_log, *, after_event_id=0, limit=500):
        """Import denied decisions from the authoritative audit repository.

        The source may be local SQLite or shared PostgreSQL. Replaying a page is
        safe because monitor signal IDs are deterministic and deduplicated.
        """
        if (not callable(getattr(audit_log, "event_page", None))
                or type(after_event_id) is not int or after_event_id < 0
                or type(limit) is not int or not 1 <= limit <= 5000):
            raise ValueError("authorization_audit_source_invalid")
        rows = audit_log.event_page(after_event_id=after_event_id, limit=limit)
        highest = after_event_id
        for row in rows:
            event_id = row.get("event_id")
            if type(event_id) is not int or event_id <= after_event_id:
                continue
            highest = max(highest, event_id)
            if row.get("allowed") != 0:
                continue
            stamp = datetime.fromisoformat(row["occurred_at"])
            if stamp.tzinfo is None:
                raise ValueError("authorization_audit_timestamp_invalid")
            subject_hash = row.get("principal_hash")
            if not isinstance(subject_hash, str) or not _HASH.fullmatch(subject_hash):
                raise ValueError("authorization_audit_subject_hash_invalid")
            reason_code = row.get("reason_code")
            reason = reason_code if isinstance(reason_code, str) and _REASON.fullmatch(reason_code) else "authorization_denied"
            self.record(SecuritySignal(
                "authorization-audit:" + str(event_id), "authorization_denied",
                stamp.timestamp(), reason, subject_hash, "authorization_audit",
            ))
        return highest

    def ingest_key_lifecycle(self, registry, administrator, *, after_event_id=0, limit=500):
        """Import key revocations from the registry's protected audit reader."""
        if type(after_event_id) is not int or after_event_id<0 or type(limit) is not int or not 1<=limit<=5000:
            raise ValueError("key_audit_cursor_invalid")
        rows=registry.audit_events(administrator,after_event_id=after_event_id,limit=limit)
        highest=after_event_id
        for row in rows:
            event_id=row.get("event_id")
            if type(event_id) is not int or event_id<=after_event_id:
                continue
            highest=max(highest,event_id)
            # The registry records the terminal state as the action ("revoked").
            # Accept the legacy verb as well for previously persisted audit rows.
            if row.get("action") not in {"revoked", "revoke"}:
                continue
            subject=hashlib.sha256((row["issuer"]+":"+row["key_id"]).encode()).hexdigest()
            self.record(SecuritySignal("workload-key-audit:"+str(event_id),"key_revoked",
                float(row["occurred_at"]),"key_revoked",subject,"workload_key_registry"))
        return highest

    def _upsert(self,db,fingerprint,s,severity,count):
        iid=hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
        row=db.execute("SELECT event_count FROM security_incidents WHERE incident_id=?",(iid,)).fetchone()
        if row is None:
            db.execute("INSERT INTO security_incidents(incident_id,fingerprint,category,severity,status,first_seen,last_seen,event_count,last_reason_code) VALUES(?,?,?,?,?,?,?,?,?)",
                (iid,fingerprint,s.category,severity,"open",float(s.occurred_at),float(s.occurred_at),count,s.reason_code))
        else:
            db.execute("UPDATE security_incidents SET last_seen=?,event_count=?,last_reason_code=?,status=CASE WHEN status='resolved' THEN 'open' ELSE status END WHERE incident_id=?",
                (float(s.occurred_at),max(int(row["event_count"]),count),s.reason_code,iid))
        return dict(db.execute("SELECT * FROM security_incidents WHERE incident_id=?",(iid,)).fetchone())
    def _authorize(self,operator):
        if not isinstance(operator,str) or not operator or len(operator)>256 or operator!=operator.strip(): raise PermissionError("security_operator_invalid")
        try: allowed=self.authorize_operator(operator)
        except Exception as exc: raise PermissionError("security_operator_authorization_unavailable") from exc
        if allowed is not True: raise PermissionError("security_operator_forbidden")
        return hashlib.sha256(operator.encode()).hexdigest()
    def incidents(self,operator,*,status=None,limit=100):
        self._authorize(operator)
        if status is not None and status not in {"open","acknowledged","resolved"}: raise ValueError("security_incident_status_invalid")
        if type(limit) is not int or not 1<=limit<=500: raise ValueError("security_incident_limit_invalid")
        db=self._connect()
        try:
            rows=db.execute("SELECT * FROM security_incidents"+(" WHERE status=?" if status else "")+" ORDER BY last_seen DESC LIMIT ?",((status,limit) if status else (limit,))).fetchall()
            return tuple(dict(r) for r in rows)
        finally: db.close()
    def transition(self,operator,incident_id,action):
        op_hash=self._authorize(operator)
        if not isinstance(incident_id,str) or not re.fullmatch(r"[0-9a-f]{32}",incident_id): raise ValueError("security_incident_id_invalid")
        if action not in {"acknowledge","resolve"}: raise ValueError("security_incident_action_invalid")
        db=self._connect()
        try:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT status FROM security_incidents WHERE incident_id=?",(incident_id,)).fetchone()
            if row is None: raise KeyError("security_incident_not_found")
            if row["status"]=="resolved": raise ValueError("security_incident_already_resolved")
            status="acknowledged" if action=="acknowledge" else "resolved"
            db.execute("UPDATE security_incidents SET status=?,updated_by=? WHERE incident_id=?",(status,op_hash,incident_id))
            db.execute("INSERT INTO security_incident_history(incident_id,action,operator_hash,occurred_at) VALUES(?,?,?,?)",(incident_id,action,op_hash,self._now()))
            db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()


    def close(self):
        """Compatibility lifecycle hook; SQLite connections are operation-scoped."""
        return None
