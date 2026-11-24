"""Provider-neutral durable outbox for security incident notifications.

Delivery is at-least-once: providers should use alert_id as their idempotency key.
No provider credentials or network clients are stored by this module.
"""
from __future__ import annotations
from contextlib import closing
import hashlib, json, math, re, sqlite3, time, uuid
from typing import Callable
from core.sqlite_file_security import prepare_private_sqlite_path

_ID=re.compile(r"^[0-9a-f]{32}$")
_SAFE=re.compile(r"^[a-zA-Z0-9_.:-]{1,96}$")
_STATES=frozenset({"pending","inflight","delivered","dead"})

class SecurityAlertOutbox:
    def __init__(self,database_path:str,*,authorize_operator:Callable[[str],bool],
                 clock:Callable[[],float]=time.time,base_retry_seconds:int=5,
                 max_retry_seconds:int=3600,max_attempts:int=12,lease_seconds:int=60):
        if not callable(authorize_operator) or not callable(clock): raise TypeError("alert_outbox_dependencies_required")
        for name,value,low,high in (("base_retry_seconds",base_retry_seconds,1,3600),
            ("max_retry_seconds",max_retry_seconds,1,86400),("max_attempts",max_attempts,1,100),
            ("lease_seconds",lease_seconds,5,3600)):
            if type(value) is not int or not low<=value<=high: raise ValueError("alert_outbox_"+name+"_invalid")
        if max_retry_seconds<base_retry_seconds: raise ValueError("alert_outbox_retry_bounds_invalid")
        self.database_path=prepare_private_sqlite_path(database_path); self.authorize_operator=authorize_operator
        self.clock=clock; self.base_retry_seconds=base_retry_seconds; self.max_retry_seconds=max_retry_seconds
        self.max_attempts=max_attempts; self.lease_seconds=lease_seconds
        with closing(self._connect()) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS security_alert_outbox(
              alert_id TEXT PRIMARY KEY,incident_id TEXT NOT NULL,payload TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('pending','inflight','delivered','dead')),
              attempts INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,available_at REAL NOT NULL,
              lease_owner TEXT,lease_token TEXT,lease_until REAL,last_error_code TEXT,delivered_at REAL);
            CREATE INDEX IF NOT EXISTS security_alert_due ON security_alert_outbox(state,available_at,lease_until);
            CREATE TRIGGER IF NOT EXISTS security_alert_payload_immutable BEFORE UPDATE OF alert_id,incident_id,payload,created_at ON security_alert_outbox
              BEGIN SELECT RAISE(ABORT,'security_alert_payload_immutable'); END;
            """)
    def _connect(self):
        db=sqlite3.connect(self.database_path,timeout=10.0,isolation_level=None); db.row_factory=sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000"); return db
    def _now(self):
        v=self.clock()
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v): raise ValueError("alert_outbox_clock_invalid")
        return float(v)
    @staticmethod
    def _incident_payload(incident):
        if not isinstance(incident,dict): raise TypeError("security_incident_dict_required")
        required={"incident_id","category","severity","status","first_seen","last_seen","event_count","last_reason_code"}
        if not required.issubset(incident): raise ValueError("security_incident_fields_missing")
        iid=incident["incident_id"]
        if not isinstance(iid,str) or not _ID.fullmatch(iid): raise ValueError("security_incident_id_invalid")
        if incident["category"] not in {"authorization_denied","admin_auth_denied","credential_verification_failed","key_revoked","audit_integrity_failure"}: raise ValueError("security_incident_category_invalid")
        if incident["severity"] not in {"high","critical"} or incident["status"] not in {"open","acknowledged","resolved"}: raise ValueError("security_incident_state_invalid")
        if not isinstance(incident["last_reason_code"],str) or not _SAFE.fullmatch(incident["last_reason_code"]): raise ValueError("security_incident_reason_invalid")
        if type(incident["event_count"]) is not int or incident["event_count"]<1: raise ValueError("security_incident_count_invalid")
        for key in ("first_seen","last_seen"):
            if isinstance(incident[key],bool) or not isinstance(incident[key],(int,float)) or not math.isfinite(incident[key]): raise ValueError("security_incident_timestamp_invalid")
        return {k:incident[k] for k in sorted(required)}
    def enqueue_incident(self,incident)->str:
        payload=self._incident_payload(incident)
        encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False)
        identity=":".join((payload["incident_id"],str(payload["last_seen"]),payload["status"],str(payload["event_count"])))
        alert_id=hashlib.sha256(identity.encode()).hexdigest()[:32]
        now=self._now(); db=self._connect()
        try:
            db.execute("INSERT OR IGNORE INTO security_alert_outbox(alert_id,incident_id,payload,state,created_at,available_at) VALUES(?,?,?,'pending',?,?)",
                       (alert_id,payload["incident_id"],encoded,now,now))
            return alert_id
        finally: db.close()
    def claim(self,worker_id:str)->dict|None:
        if not isinstance(worker_id,str) or not _SAFE.fullmatch(worker_id): raise ValueError("alert_worker_id_invalid")
        now=self._now(); db=self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("""SELECT * FROM security_alert_outbox WHERE
                (state='pending' AND available_at<=?) OR (state='inflight' AND lease_until<=?)
                ORDER BY available_at,created_at LIMIT 1""",(now,now)).fetchone()
            if row is None: db.commit(); return None
            attempts=int(row["attempts"])+1
            lease_token=uuid.uuid4().hex
            db.execute("""UPDATE security_alert_outbox SET state='inflight',attempts=?,lease_owner=?,lease_token=?,lease_until=?
                WHERE alert_id=?""",(attempts,worker_id,lease_token,now+self.lease_seconds,row["alert_id"]))
            db.commit()
            return {"alert_id":row["alert_id"],"payload":json.loads(row["payload"]),"attempt":attempts,"lease_token":lease_token}
        except Exception: db.rollback(); raise
        finally: db.close()
    def complete(self,worker_id:str,alert_id:str,*,lease_token:str,success:bool,error_code:str="delivery_failed")->str:
        if not isinstance(worker_id,str) or not _SAFE.fullmatch(worker_id): raise ValueError("alert_worker_id_invalid")
        if not isinstance(alert_id,str) or not _ID.fullmatch(alert_id): raise ValueError("alert_id_invalid")
        if not isinstance(lease_token,str) or not re.fullmatch(r"[0-9a-f]{32}",lease_token): raise ValueError("alert_lease_token_invalid")
        if type(success) is not bool: raise TypeError("alert_delivery_result_must_be_boolean")
        if not isinstance(error_code,str) or not _SAFE.fullmatch(error_code): raise ValueError("alert_error_code_invalid")
        now=self._now(); db=self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT attempts,state,lease_owner,lease_token,lease_until FROM security_alert_outbox WHERE alert_id=?",(alert_id,)).fetchone()
            if row is None: raise KeyError("alert_not_found")
            if row["state"]!="inflight" or row["lease_owner"]!=worker_id or row["lease_token"]!=lease_token or float(row["lease_until"])<=now: raise PermissionError("alert_lease_not_owned")
            if success:
                db.execute("UPDATE security_alert_outbox SET state='delivered',delivered_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error_code=NULL WHERE alert_id=?",(now,alert_id))
                result="delivered"
            elif int(row["attempts"])>=self.max_attempts:
                db.execute("UPDATE security_alert_outbox SET state='dead',lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error_code=? WHERE alert_id=?",(error_code,alert_id))
                result="dead"
            else:
                delay=min(self.max_retry_seconds,self.base_retry_seconds*(2**min(int(row["attempts"])-1,20)))
                db.execute("UPDATE security_alert_outbox SET state='pending',available_at=?,lease_owner=NULL,lease_until=NULL,last_error_code=? WHERE alert_id=?",(now+delay,error_code,alert_id))
                result="pending"
            db.commit(); return result
        except Exception: db.rollback(); raise
        finally: db.close()
    def deliver_one(self,worker_id:str,sender:Callable[[dict,str],bool])->str:
        if not callable(sender): raise TypeError("alert_sender_required")
        item=self.claim(worker_id)
        if item is None: return "empty"
        try:
            success=sender(item["payload"],item["alert_id"])
            if type(success) is not bool: raise TypeError("alert_sender_must_return_boolean")
            return self.complete(worker_id,item["alert_id"],lease_token=item["lease_token"],success=success,
                                 error_code="provider_rejected")
        except Exception as exc:
            # Do not persist exception text: it may contain endpoint or credential data.
            try: return self.complete(worker_id,item["alert_id"],lease_token=item["lease_token"],success=False,error_code="provider_error")
            except PermissionError: raise
    def list_alerts(self,operator:str,*,state:str|None=None,limit:int=100)->tuple[dict,...]:
        if not isinstance(operator,str) or not operator or len(operator)>256 or operator!=operator.strip(): raise PermissionError("alert_operator_invalid")
        try: allowed=self.authorize_operator(operator)
        except Exception as exc: raise PermissionError("alert_operator_authorization_unavailable") from exc
        if allowed is not True: raise PermissionError("alert_operator_forbidden")
        if state is not None and state not in _STATES: raise ValueError("alert_state_invalid")
        if type(limit) is not int or not 1<=limit<=500: raise ValueError("alert_limit_invalid")
        db=self._connect()
        try:
            rows=db.execute("SELECT alert_id,incident_id,state,attempts,created_at,available_at,last_error_code,delivered_at FROM security_alert_outbox"+(" WHERE state=?" if state else "")+" ORDER BY created_at DESC LIMIT ?",((state,limit) if state else (limit,))).fetchall()
            return tuple(dict(r) for r in rows)
        finally: db.close()
