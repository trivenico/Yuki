"""Persistent, owner-scoped inference jobs for paired Yuki devices."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
import hashlib, hmac, re, secrets, sqlite3, uuid
from pathlib import Path
from core.device_pairing import DevicePairingService
from core.device_resource_reservations import DeviceResourceReservationService, StaleReservation
from core.sqlite_file_security import prepare_private_sqlite_path
_MODEL=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_DEVICE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
def _now(): return datetime.now(timezone.utc)
def _stamp(v): return v.astimezone(timezone.utc).isoformat()
@dataclass(frozen=True)
class InferenceJob:
 job_id:str; owner_id:str; model_id:str; device_id:str; status:str; input_text:str|None; result_text:str|None; error_code:str|None; created_at:str; updated_at:str; lease_expires_at:str|None; reservation_id:str|None; reservation_fencing_token:int|None; lease_token:str|None=field(default=None, repr=False, compare=False)
 def public_dict(self):
  d=asdict(self); d.pop("input_text"); d.pop("lease_token", None); return d
class InferenceJobService:
 def __init__(self,database_path,pairing,*,lease_seconds=120,max_jobs_per_owner=1000,reservation_service=None):
  if not isinstance(pairing,DevicePairingService): raise TypeError("pairing must be a DevicePairingService")
  if type(lease_seconds)!=int or not 15<=lease_seconds<=3600: raise ValueError("invalid_lease_seconds")
  if type(max_jobs_per_owner)!=int or not 1<=max_jobs_per_owner<=100000: raise ValueError("invalid_max_jobs_per_owner")
  if reservation_service is not None and (not isinstance(reservation_service,DeviceResourceReservationService) or reservation_service.pairing_service is not pairing): raise ValueError("reservation_service_must_share_pairing_service")
  self.database_path=prepare_private_sqlite_path(database_path); self.pairing=pairing; self.lease_seconds=lease_seconds; self.max_jobs_per_owner=max_jobs_per_owner; self.reservation_service=reservation_service
  if Path(self.database_path).resolve() != Path(pairing.database_path).expanduser().resolve():
   raise ValueError("inference_jobs_and_device_pairing_must_share_database")
  Path(self.database_path).parent.mkdir(parents=True,exist_ok=True)
  with self._connect() as db:
   db.execute("PRAGMA journal_mode=WAL")
   db.execute("""CREATE TABLE IF NOT EXISTS inference_jobs(job_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,model_id TEXT NOT NULL,device_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),input_text TEXT NOT NULL,result_text TEXT,error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,lease_expires_at TEXT,lease_token_hash TEXT,reservation_id TEXT,reservation_fencing_token INTEGER)""")
   columns={row[1] for row in db.execute("PRAGMA table_info(inference_jobs)")}
   if "lease_token_hash" not in columns: db.execute("ALTER TABLE inference_jobs ADD COLUMN lease_token_hash TEXT")
   if "reservation_id" not in columns: db.execute("ALTER TABLE inference_jobs ADD COLUMN reservation_id TEXT")
   if "reservation_fencing_token" not in columns: db.execute("ALTER TABLE inference_jobs ADD COLUMN reservation_fencing_token INTEGER")
   db.execute("""CREATE TABLE IF NOT EXISTS inference_reservation_operations(
    operation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, job_id TEXT NOT NULL,
    device_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    memory_bytes INTEGER NOT NULL, cpu_millicores INTEGER NOT NULL,
    gpu_memory_bytes INTEGER NOT NULL, state TEXT NOT NULL
      CHECK(state IN ('pending','completed','abandoned')),
    reservation_id TEXT, fencing_token INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(owner_id,idempotency_key))""")
   op_columns={row[1] for row in db.execute("PRAGMA table_info(inference_reservation_operations)")}
   if "operation_type" not in op_columns:
    db.execute("ALTER TABLE inference_reservation_operations ADD COLUMN operation_type TEXT NOT NULL DEFAULT 'reserve'")
   db.execute("CREATE INDEX IF NOT EXISTS idx_reservation_ops_state ON inference_reservation_operations(state,updated_at)")
   db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_owner ON inference_jobs(owner_id,created_at)")
   db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_device_state ON inference_jobs(device_id,status,created_at)")
 def _connect(self):
  db=sqlite3.connect(self.database_path,timeout=5)
  db.row_factory=sqlite3.Row
  db.execute("PRAGMA busy_timeout=5000")
  return _Connection(db)
 @staticmethod
 def _job(row, *, lease_token=None):
  values=dict(row); values.pop("lease_token_hash", None); values["lease_token"]=lease_token
  return InferenceJob(**values)
 def _device(self,token):
  d=self.pairing.authenticate_device(token)
  if d is None: raise PermissionError("invalid_device_credential")
  return d
 def create_job(self,owner_id,*,model_id,device_id,input_text):
  if not isinstance(owner_id,str) or not owner_id.strip() or len(owner_id)>128: raise ValueError("invalid_owner")
  if not isinstance(model_id,str) or not _MODEL.fullmatch(model_id): raise ValueError("invalid_model_id")
  if not isinstance(device_id,str) or not _DEVICE.fullmatch(device_id): raise ValueError("invalid_device_id")
  if not isinstance(input_text,str) or not input_text.strip() or len(input_text.encode())>64000: raise ValueError("invalid_input")
  now=_now(); stamp=_stamp(now); jid=uuid.uuid4().hex
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   if not self.pairing.owns_active_device_in_transaction(db, owner_id, device_id):
    raise PermissionError("device_not_owned_or_paired")
   count=db.execute("SELECT COUNT(*) FROM inference_jobs WHERE owner_id=? AND status IN ('queued','running')",(owner_id,)).fetchone()[0]
   if count>=self.max_jobs_per_owner: raise ValueError("owner_job_limit_reached")
   db.execute("INSERT INTO inference_jobs(job_id,owner_id,model_id,device_id,status,input_text,result_text,error_code,created_at,updated_at,lease_expires_at,lease_token_hash,reservation_id,reservation_fencing_token) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",(jid,owner_id,model_id,device_id,"queued",input_text,None,None,stamp,stamp,None))
   row=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(jid,)).fetchone()
  return self._job(row)


 def begin_reservation_operation(self,owner_id,job_id,*,idempotency_key,memory_bytes,cpu_millicores,gpu_memory_bytes):
  """Durably record intent before touching the separate reservation ledger."""
  if self.reservation_service is None: raise RuntimeError("reservation_service_required")
  if not isinstance(job_id,str) or not re.fullmatch(r"[0-9a-f]{32}",job_id): raise ValueError("invalid_job_id")
  if not isinstance(idempotency_key,str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._:-]{0,127}",idempotency_key): raise ValueError("invalid_idempotency_key")
  amounts=(memory_bytes,cpu_millicores,gpu_memory_bytes)
  if any(type(v) is not int or v<0 or v>2**60 for v in amounts): raise ValueError("invalid_reservation_amount")
  now=_stamp(_now()); op_id=uuid.uuid4().hex
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   job=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
   if job is None: raise LookupError("job_not_found")
   old=db.execute("SELECT * FROM inference_reservation_operations WHERE owner_id=? AND idempotency_key=?",(owner_id,idempotency_key)).fetchone()
   if old:
    same=(old["job_id"],old["device_id"],old["memory_bytes"],old["cpu_millicores"],old["gpu_memory_bytes"])==(job_id,job["device_id"],*amounts)
    if not same: raise ValueError("reservation_idempotency_conflict")
    if old["state"]=="abandoned": raise ValueError("reservation_operation_abandoned_use_new_key")
    return dict(old)
   if job["status"]!="queued": raise ValueError("job_not_reservable")
   if job["reservation_id"] is not None: raise ValueError("job_already_reserved")
   db.execute("""INSERT INTO inference_reservation_operations
    (operation_id,owner_id,job_id,device_id,idempotency_key,memory_bytes,cpu_millicores,gpu_memory_bytes,state,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?, 'pending',?,?)""",
    (op_id,owner_id,job_id,job["device_id"],idempotency_key,*amounts,now,now))
   return dict(db.execute("SELECT * FROM inference_reservation_operations WHERE operation_id=?",(op_id,)).fetchone())
 def complete_reservation_operation(self,owner_id,job_id,idempotency_key,reservation_id,fencing_token):
  now=_stamp(_now())
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   op=db.execute("SELECT * FROM inference_reservation_operations WHERE owner_id=? AND job_id=? AND idempotency_key=?",(owner_id,job_id,idempotency_key)).fetchone()
   job=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
   if op is None or job is None: raise LookupError("reservation_operation_not_found")
   if job["reservation_id"]!=reservation_id or job["reservation_fencing_token"]!=fencing_token: raise ValueError("reservation_binding_mismatch")
   db.execute("""UPDATE inference_reservation_operations SET state='completed',reservation_id=?,fencing_token=?,updated_at=?
    WHERE operation_id=? AND state='pending'""",(reservation_id,fencing_token,now,op["operation_id"]))
 def reconcile_reservation_operations(self,*,limit=100,stale_after_seconds=60):
  """Reconcile crash-interrupted reservation sagas; never requeue inference work."""
  if self.reservation_service is None: raise RuntimeError("reservation_service_required")
  if type(limit) is not int or not 1<=limit<=1000: raise ValueError("invalid_limit")
  if type(stale_after_seconds) is not int or not 1<=stale_after_seconds<=86400: raise ValueError("invalid_stale_after_seconds")
  now_dt=_now(); now=_stamp(now_dt); cutoff=_stamp(now_dt-timedelta(seconds=stale_after_seconds))
  resolved={"completed":0,"abandoned":0,"deferred":0}
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   ops=db.execute("""SELECT * FROM inference_reservation_operations
    WHERE state='pending' AND updated_at<=? ORDER BY created_at LIMIT ?""",(cutoff,limit)).fetchall()
   for op in ops:
    ledger=self.reservation_service.get_reservation_by_idempotency_key(op["owner_id"],op["idempotency_key"])
    job=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(op["owner_id"],op["job_id"])).fetchone()
    if op["operation_type"]=="cancel":
     reservation_id=op["reservation_id"] or (job["reservation_id"] if job is not None else None)
     fence=op["fencing_token"] or (job["reservation_fencing_token"] if job is not None else None)
     lease=self.reservation_service.get_reservation_by_id(op["owner_id"],reservation_id) if reservation_id else None
     def finish_cancel(_row=None):
      if job is not None:
       db.execute("UPDATE inference_jobs SET status='cancelled',lease_expires_at=NULL,lease_token_hash=NULL,updated_at=? WHERE owner_id=? AND job_id=? AND status IN ('queued','running')",(now,op["owner_id"],op["job_id"]))
      db.execute("UPDATE inference_reservation_operations SET state='completed',updated_at=? WHERE operation_id=? AND state='pending'",(now,op["operation_id"]))
     if lease is not None and lease["status"]=="active":
      try:
       self.reservation_service.release_active_reservation(op["owner_id"],lease["device_id"],lease["task_id"],lease["reservation_id"],int(lease["fencing_token"]),finish_cancel)
      except StaleReservation:
       finish_cancel()
     else:
      finish_cancel()
     resolved["completed"]+=1
     continue
    if ledger is None:
     db.execute("UPDATE inference_reservation_operations SET state='abandoned',updated_at=? WHERE operation_id=? AND state='pending'",(now,op["operation_id"]))
     resolved["abandoned"]+=1
     continue
    if (ledger["device_id"]!=op["device_id"] or ledger["task_id"]!=op["job_id"]
        or (ledger["memory_bytes"],ledger["cpu_millicores"],ledger["gpu_memory_bytes"])!=(op["memory_bytes"],op["cpu_millicores"],op["gpu_memory_bytes"])):
     db.execute("UPDATE inference_reservation_operations SET state='abandoned',updated_at=? WHERE operation_id=? AND state='pending'",(now,op["operation_id"]))
     resolved["abandoned"]+=1
     continue
    if (job is not None and job["reservation_id"]==ledger["reservation_id"]
        and job["reservation_fencing_token"]==ledger["fencing_token"]
        and job["status"] in ("queued","running") and ledger["status"]=="active"):
     db.execute("""UPDATE inference_reservation_operations SET state='completed',reservation_id=?,fencing_token=?,updated_at=?
      WHERE operation_id=? AND state='pending'""",(ledger["reservation_id"],ledger["fencing_token"],now,op["operation_id"]))
     resolved["completed"]+=1
     continue
    if ledger["status"]=="active":
     try:
      self.reservation_service.release_active_reservation(
       op["owner_id"],op["device_id"],op["job_id"],ledger["reservation_id"],
       int(ledger["fencing_token"]),
       lambda _row: db.execute("""UPDATE inference_reservation_operations
        SET state='abandoned',reservation_id=?,fencing_token=?,updated_at=?
        WHERE operation_id=? AND state='pending'""",
        (ledger["reservation_id"],ledger["fencing_token"],now,op["operation_id"])),
      )
     except StaleReservation:
      db.execute("UPDATE inference_reservation_operations SET state='abandoned',updated_at=? WHERE operation_id=? AND state='pending'",(now,op["operation_id"]))
     resolved["abandoned"]+=1
    else:
     db.execute("""UPDATE inference_reservation_operations SET state='abandoned',reservation_id=?,fencing_token=?,updated_at=?
      WHERE operation_id=? AND state='pending'""",(ledger["reservation_id"],ledger["fencing_token"],now,op["operation_id"]))
     resolved["abandoned"]+=1
  return resolved
 def bind_reservation(self,owner_id,job_id,*,reservation_id,fencing_token):
  """Bind a queued job to its exact active reservation generation."""
  if self.reservation_service is None: raise RuntimeError("reservation_service_required")
  if not isinstance(job_id,str) or not re.fullmatch(r"[0-9a-f]{32}",job_id): raise ValueError("invalid_job_id")
  stamp=_stamp(_now())
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   row=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
   if row is None: raise LookupError("job_not_found")
   if row["status"]!="queued": raise ValueError("job_not_bindable")
   if row["reservation_id"] is not None:
    if row["reservation_id"]==reservation_id and row["reservation_fencing_token"]==fencing_token:
     if not self.pairing.owns_active_device_in_transaction(db,owner_id,row["device_id"]):
      raise PermissionError("device_not_owned_or_paired")
     current=self.reservation_service.with_active_reservation(
      owner_id,row["device_id"],job_id,reservation_id,fencing_token,lambda _reservation: row,
     )
     return self._job(current)
    raise ValueError("job_not_bindable")
   if not self.pairing.owns_active_device_in_transaction(db,owner_id,row["device_id"]):
    raise PermissionError("device_not_owned_or_paired")
   def bind(_reservation):
    changed=db.execute(
     "UPDATE inference_jobs SET reservation_id=?,reservation_fencing_token=?,updated_at=? WHERE job_id=? AND status='queued' AND reservation_id IS NULL",
     (reservation_id,fencing_token,stamp,job_id),
    ).rowcount
    if changed!=1: raise ValueError("job_not_bindable")
    return db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(job_id,)).fetchone()
   bound=self.reservation_service.with_active_reservation(
    owner_id,row["device_id"],job_id,reservation_id,fencing_token,bind,
   )
  return self._job(bound)
 def _with_reservation(self,db,row,operation):
  if self.reservation_service is None or not row["reservation_id"] or row["reservation_fencing_token"] is None:
   raise ValueError("job_reservation_required")
  return self.reservation_service.with_active_reservation(
   row["owner_id"],row["device_id"],row["job_id"],
   row["reservation_id"],int(row["reservation_fencing_token"]),operation,
  )
 def claim_next(self,device_token):
  # Authenticate before allowing a worker poll to trigger recovery side effects.
  self._device(device_token)
  if self.reservation_service is not None:
   self.reconcile_reservation_operations()
  now=_now(); stamp=_stamp(now); expiry=_stamp(now+timedelta(seconds=self.lease_seconds))
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   device=self.pairing.authenticate_device_in_transaction(db, device_token)
   if device is None: raise PermissionError("invalid_device_credential")
   db.execute("UPDATE inference_jobs SET status='failed',error_code='lease_expired',lease_expires_at=NULL,lease_token_hash=NULL,updated_at=? WHERE device_id=? AND status='running' AND lease_expires_at<=?",(stamp,device.device_id,stamp))
   row=db.execute("SELECT * FROM inference_jobs WHERE device_id=? AND status='queued' AND reservation_id IS NOT NULL AND reservation_fencing_token IS NOT NULL ORDER BY created_at,job_id LIMIT 1",(device.device_id,)).fetchone()
   if row is None: return None
   if not row["reservation_id"] or row["reservation_fencing_token"] is None:
    return None
   lease_token=secrets.token_urlsafe(32); lease_token_hash=hashlib.sha256(lease_token.encode("ascii")).hexdigest()
   def claim(_reservation):
    changed=db.execute("UPDATE inference_jobs SET status='running',lease_expires_at=?,lease_token_hash=?,updated_at=? WHERE job_id=? AND status='queued' AND reservation_id=? AND reservation_fencing_token=?",(expiry,lease_token_hash,stamp,row["job_id"],row["reservation_id"],row["reservation_fencing_token"])).rowcount
    if changed!=1: return None
    return db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(row["job_id"],)).fetchone()
   try:
    claimed=self._with_reservation(db,row,claim)
   except StaleReservation:
    db.execute("UPDATE inference_jobs SET status='failed',error_code='reservation_invalid',updated_at=? WHERE job_id=? AND status='queued'",(stamp,row["job_id"]))
    return None
   if claimed is None: return None
  return self._job(claimed, lease_token=lease_token)
 def renew_job_lease(self,device_token,job_id,*,lease_token):
  """Extend a live claim without changing its fencing credential.

  Renewal is serialized with device authentication/revocation in the shared
  pairing/jobs database. Expired, cancelled, terminal, or superseded claims
  cannot be resurrected.
  """
  if not isinstance(job_id,str) or not re.fullmatch(r"[0-9a-f]{32}",job_id):
   raise ValueError("invalid_job_id")
  if not isinstance(lease_token,str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}",lease_token):
   raise ValueError("invalid_lease_token")
  now=_now(); stamp=_stamp(now); expiry=_stamp(now+timedelta(seconds=self.lease_seconds))
  supplied_hash=hashlib.sha256(lease_token.encode("ascii")).hexdigest()
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   device=self.pairing.authenticate_device_in_transaction(db, device_token)
   if device is None: raise PermissionError("invalid_device_credential")
   row=db.execute("SELECT * FROM inference_jobs WHERE job_id=? AND device_id=?",(job_id,device.device_id)).fetchone()
   if row is None: raise LookupError("job_not_found")
   stored_hash=row["lease_token_hash"] or ""
   if (row["status"]!="running" or not row["lease_expires_at"]
       or row["lease_expires_at"]<=stamp
       or not hmac.compare_digest(stored_hash,supplied_hash)):
    raise ValueError("job_lease_expired_or_not_owned")
   changed=self._with_reservation(db,row,lambda _reservation: db.execute(
    "UPDATE inference_jobs SET lease_expires_at=?,updated_at=? WHERE job_id=? AND device_id=? AND status='running' AND lease_expires_at>? AND lease_token_hash=?",
    (expiry,stamp,job_id,device.device_id,stamp,supplied_hash),
   ).rowcount)
   if changed!=1: raise ValueError("job_lease_expired_or_not_owned")
   updated=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(job_id,)).fetchone()
  return self._job(updated,lease_token=lease_token)
 def complete_job(self,device_token,job_id,*,lease_token,result_text=None,error_code=None):
  if not isinstance(job_id,str) or not re.fullmatch(r"[0-9a-f]{32}",job_id): raise ValueError("invalid_job_id")
  if not isinstance(lease_token,str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}",lease_token): raise ValueError("invalid_lease_token")
  if (result_text is None)==(error_code is None): raise ValueError("provide_exactly_one_result_or_error")
  if result_text is not None and (not isinstance(result_text,str) or len(result_text.encode())>256000): raise ValueError("invalid_result")
  if error_code is not None and (not isinstance(error_code,str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",error_code)): raise ValueError("invalid_error_code")
  stamp=_stamp(_now())
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   device=self.pairing.authenticate_device_in_transaction(db, device_token)
   if device is None: raise PermissionError("invalid_device_credential")
   row=db.execute("SELECT * FROM inference_jobs WHERE job_id=? AND device_id=?",(job_id,device.device_id)).fetchone()
   if row is None: raise LookupError("job_not_found")
   supplied_hash=hashlib.sha256(lease_token.encode("ascii")).hexdigest()
   stored_hash=row["lease_token_hash"] or ""
   if row["status"]!="running" or not row["lease_expires_at"] or row["lease_expires_at"]<=stamp or not hmac.compare_digest(stored_hash,supplied_hash): raise ValueError("job_lease_expired_or_not_owned")
   state="succeeded" if result_text is not None else "failed"
   changed=self._with_reservation(db,row,lambda _reservation: db.execute(
    "UPDATE inference_jobs SET status=?,result_text=?,error_code=?,lease_expires_at=NULL,lease_token_hash=NULL,updated_at=? WHERE job_id=? AND status='running' AND lease_token_hash=?",
    (state,result_text,error_code,stamp,job_id,supplied_hash),
   ).rowcount)
   if changed!=1: raise ValueError("job_lease_expired_or_not_owned")
   updated=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(job_id,)).fetchone()
  return self._job(updated)
 def cancel_job(self,owner_id,job_id):
  """Durably journal cancellation before releasing the separate reservation."""
  stamp=_stamp(_now())
  operation_key="cancel-"+job_id+"-"+uuid.uuid4().hex
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   row=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
   if row is None or row["status"] not in ("queued","running"): return False
   db.execute("""INSERT OR IGNORE INTO inference_reservation_operations
    (operation_id,owner_id,job_id,device_id,idempotency_key,memory_bytes,cpu_millicores,gpu_memory_bytes,
     state,reservation_id,fencing_token,created_at,updated_at,operation_type)
    VALUES(?,?,?,?,?,0,0,0,'pending',?,?,?,?,'cancel')""",
    (uuid.uuid4().hex,owner_id,job_id,row["device_id"],operation_key,
     row["reservation_id"],row["reservation_fencing_token"],stamp,stamp))
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   row=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
   op=db.execute("SELECT * FROM inference_reservation_operations WHERE owner_id=? AND idempotency_key=?",(owner_id,operation_key)).fetchone()
   if row is None or row["status"] not in ("queued","running"):
    if op is not None:
     db.execute("UPDATE inference_reservation_operations SET state='completed',updated_at=? WHERE operation_id=? AND state='pending'",(stamp,op["operation_id"]))
    return False
   def cancel(_reservation=None):
    changed=db.execute(
     "UPDATE inference_jobs SET status='cancelled',lease_expires_at=NULL,lease_token_hash=NULL,updated_at=? WHERE owner_id=? AND job_id=? AND status IN ('queued','running')",
     (stamp,owner_id,job_id),
    ).rowcount
    db.execute("UPDATE inference_reservation_operations SET state='completed',updated_at=? WHERE operation_id=? AND state='pending'",(stamp,op["operation_id"]))
    return changed==1
   if self.reservation_service is not None and row["reservation_id"] and row["reservation_fencing_token"] is not None:
    try:
     return self.reservation_service.release_active_reservation(
      owner_id,row["device_id"],job_id,row["reservation_id"],
      int(row["reservation_fencing_token"]),cancel,
     )
    except StaleReservation:
     return cancel()
   return cancel()
 def get_job(self,owner_id,job_id):
  with self._connect() as db: row=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
  return self._job(row) if row else None
 def list_jobs(self,owner_id,*,limit=100):
  if type(limit)!=int or not 1<=limit<=100: raise ValueError("invalid_limit")
  with self._connect() as db: rows=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",(owner_id,limit)).fetchall()
  return [self._job(r) for r in rows]


class _Connection:
 """Commit/rollback and always close SQLite connections on every platform."""
 def __init__(self, connection):
  self.connection=connection
 def __enter__(self):
  return self.connection
 def __exit__(self, exc_type, exc, tb):
  try:
   if exc_type is None:
    self.connection.commit()
   else:
    self.connection.rollback()
  finally:
   self.connection.close()
  return False
