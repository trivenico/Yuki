"""Persistent, owner-scoped inference jobs for paired Yuki devices."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
import hashlib, hmac, re, secrets, sqlite3, uuid
from pathlib import Path
from core.device_pairing import DevicePairingService
from core.sqlite_file_security import prepare_private_sqlite_path
_MODEL=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_DEVICE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
def _now(): return datetime.now(timezone.utc)
def _stamp(v): return v.astimezone(timezone.utc).isoformat()
@dataclass(frozen=True)
class InferenceJob:
 job_id:str; owner_id:str; model_id:str; device_id:str; status:str; input_text:str|None; result_text:str|None; error_code:str|None; created_at:str; updated_at:str; lease_expires_at:str|None; lease_token:str|None=field(default=None, repr=False, compare=False)
 def public_dict(self):
  d=asdict(self); d.pop("input_text"); d.pop("lease_token", None); return d
class InferenceJobService:
 def __init__(self,database_path,pairing,*,lease_seconds=120,max_jobs_per_owner=1000):
  if not isinstance(pairing,DevicePairingService): raise TypeError("pairing must be a DevicePairingService")
  if type(lease_seconds)!=int or not 15<=lease_seconds<=3600: raise ValueError("invalid_lease_seconds")
  if type(max_jobs_per_owner)!=int or not 1<=max_jobs_per_owner<=100000: raise ValueError("invalid_max_jobs_per_owner")
  self.database_path=prepare_private_sqlite_path(database_path); self.pairing=pairing; self.lease_seconds=lease_seconds; self.max_jobs_per_owner=max_jobs_per_owner
  if Path(self.database_path).resolve() != Path(pairing.database_path).expanduser().resolve():
   raise ValueError("inference_jobs_and_device_pairing_must_share_database")
  Path(self.database_path).parent.mkdir(parents=True,exist_ok=True)
  with self._connect() as db:
   db.execute("PRAGMA journal_mode=WAL")
   db.execute("""CREATE TABLE IF NOT EXISTS inference_jobs(job_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,model_id TEXT NOT NULL,device_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),input_text TEXT NOT NULL,result_text TEXT,error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,lease_expires_at TEXT,lease_token_hash TEXT)""")
   columns={row[1] for row in db.execute("PRAGMA table_info(inference_jobs)")}
   if "lease_token_hash" not in columns: db.execute("ALTER TABLE inference_jobs ADD COLUMN lease_token_hash TEXT")
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
   db.execute("INSERT INTO inference_jobs(job_id,owner_id,model_id,device_id,status,input_text,result_text,error_code,created_at,updated_at,lease_expires_at,lease_token_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",(jid,owner_id,model_id,device_id,"queued",input_text,None,None,stamp,stamp,None))
   row=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(jid,)).fetchone()
  return self._job(row)
 def claim_next(self,device_token):
  now=_now(); stamp=_stamp(now); expiry=_stamp(now+timedelta(seconds=self.lease_seconds))
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   device=self.pairing.authenticate_device_in_transaction(db, device_token)
   if device is None: raise PermissionError("invalid_device_credential")
   db.execute("UPDATE inference_jobs SET status='queued',lease_expires_at=NULL,lease_token_hash=NULL,updated_at=? WHERE device_id=? AND status='running' AND lease_expires_at<=?",(stamp,device.device_id,stamp))
   row=db.execute("SELECT * FROM inference_jobs WHERE device_id=? AND status='queued' ORDER BY created_at,job_id LIMIT 1",(device.device_id,)).fetchone()
   if row is None: return None
   lease_token=secrets.token_urlsafe(32); lease_token_hash=hashlib.sha256(lease_token.encode("ascii")).hexdigest()
   if db.execute("UPDATE inference_jobs SET status='running',lease_expires_at=?,lease_token_hash=?,updated_at=? WHERE job_id=? AND status='queued'",(expiry,lease_token_hash,stamp,row["job_id"])).rowcount!=1: return None
   claimed=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(row["job_id"],)).fetchone()
  return self._job(claimed, lease_token=lease_token)
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
   db.execute("UPDATE inference_jobs SET status=?,result_text=?,error_code=?,lease_expires_at=NULL,lease_token_hash=NULL,updated_at=? WHERE job_id=? AND status='running' AND lease_token_hash=?",(state,result_text,error_code,stamp,job_id,supplied_hash))
   updated=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(job_id,)).fetchone()
  return self._job(updated)
 def cancel_job(self,owner_id,job_id):
  stamp=_stamp(_now())
  with self._connect() as db:
   changed=db.execute("UPDATE inference_jobs SET status='cancelled',lease_expires_at=NULL,lease_token_hash=NULL,updated_at=? WHERE owner_id=? AND job_id=? AND status IN ('queued','running')",(stamp,owner_id,job_id)).rowcount
   return changed==1
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
