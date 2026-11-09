"""Persistent, owner-scoped inference jobs for paired Yuki devices."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
import hashlib, hmac, re, secrets, sqlite3, uuid
from pathlib import Path
from core.device_pairing import DevicePairingService
_MODEL=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_DEVICE=re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._:-]{0,127}$")
def _now(): return datetime.now(timezone.utc)
def _stamp(v): return v.astimezone(timezone.utc).isoformat()
@dataclass(frozen=True)
class InferenceJob:
 job_id:str; owner_id:str; model_id:str; device_id:str; status:str; input_text:str|None; result_text:str|None; error_code:str|None; created_at:str; updated_at:str; lease_expires_at:str|None; claim_generation:int=0; claim_token:str|None=field(default=None,repr=False)
 def public_dict(self):
  d=asdict(self); d.pop("input_text"); d.pop("claim_token"); return d
class InferenceJobService:
 def __init__(self,database_path,pairing,*,lease_seconds=120,max_jobs_per_owner=1000):
  if not isinstance(pairing,DevicePairingService): raise TypeError("pairing must be a DevicePairingService")
  if type(lease_seconds)!=int or not 15<=lease_seconds<=3600: raise ValueError("invalid_lease_seconds")
  if type(max_jobs_per_owner)!=int or not 1<=max_jobs_per_owner<=100000: raise ValueError("invalid_max_jobs_per_owner")
  self.database_path=str(Path(database_path).expanduser()); self.pairing=pairing; self.lease_seconds=lease_seconds; self.max_jobs_per_owner=max_jobs_per_owner
  Path(self.database_path).parent.mkdir(parents=True,exist_ok=True)
  with self._connect() as db:
   db.execute("PRAGMA journal_mode=WAL")
   db.execute("""CREATE TABLE IF NOT EXISTS inference_jobs(job_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,model_id TEXT NOT NULL,device_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),input_text TEXT NOT NULL,result_text TEXT,error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,lease_expires_at TEXT)""")
   columns={r["name"] for r in db.execute("PRAGMA table_info(inference_jobs)")}
   if "claim_generation" not in columns: db.execute("ALTER TABLE inference_jobs ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0")
   if "claim_token_hash" not in columns: db.execute("ALTER TABLE inference_jobs ADD COLUMN claim_token_hash TEXT")
   db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_owner ON inference_jobs(owner_id,created_at)")
   db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_device_state ON inference_jobs(device_id,status,created_at)")
 def _connect(self):
  return _Connection(sqlite3.connect(self.database_path, timeout=5))

 @staticmethod
 def _job(row):
  keys=("job_id","owner_id","model_id","device_id","status","input_text","result_text","error_code","created_at","updated_at","lease_expires_at","claim_generation")
  return InferenceJob(**{k:row[k] for k in keys})
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
   count=db.execute("SELECT COUNT(*) FROM inference_jobs WHERE owner_id=? AND status IN ('queued','running')",(owner_id,)).fetchone()[0]
   if count>=self.max_jobs_per_owner: raise ValueError("owner_job_limit_reached")
   owned=self.pairing.owns_active_device(owner_id,device_id)
   if not owned: raise PermissionError("device_not_owned_or_paired")
   db.execute("INSERT INTO inference_jobs(job_id,owner_id,model_id,device_id,status,input_text,result_text,error_code,created_at,updated_at,lease_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(jid,owner_id,model_id,device_id,"queued",input_text,None,None,stamp,stamp,None))
   row=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(jid,)).fetchone()
  return self._job(row)
 def claim_next(self,device_token):
  device=self._device(device_token); now=_now(); stamp=_stamp(now); expiry=_stamp(now+timedelta(seconds=self.lease_seconds))
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   db.execute("UPDATE inference_jobs SET status='failed',error_code='lease_expired',lease_expires_at=NULL,claim_token_hash=NULL,updated_at=? WHERE device_id=? AND status='running' AND lease_expires_at<=?",(stamp,device.device_id,stamp))
   row=db.execute("SELECT * FROM inference_jobs WHERE device_id=? AND status='queued' ORDER BY created_at,job_id LIMIT 1",(device.device_id,)).fetchone()
   if row is None: return None
   token=secrets.token_urlsafe(32); digest=hashlib.sha256(token.encode("ascii")).hexdigest()
   if db.execute("UPDATE inference_jobs SET status='running',lease_expires_at=?,claim_generation=claim_generation+1,claim_token_hash=?,updated_at=? WHERE job_id=? AND status='queued'",(expiry,digest,stamp,row["job_id"])).rowcount!=1: return None
   claimed=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(row["job_id"],)).fetchone()
  from dataclasses import replace
  return replace(self._job(claimed),claim_token=token)
 def complete_job(self,device_token,job_id,*,claim_generation,claim_token,result_text=None,error_code=None):
  device=self._device(device_token)
  if not isinstance(job_id,str) or not re.fullmatch(r"[0-9a-f]{32}",job_id): raise ValueError("invalid_job_id")
  if (result_text is None)==(error_code is None): raise ValueError("provide_exactly_one_result_or_error")
  if result_text is not None and (not isinstance(result_text,str) or len(result_text.encode())>256000): raise ValueError("invalid_result")
  if error_code is not None and (not isinstance(error_code,str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",error_code)): raise ValueError("invalid_error_code")
  if type(claim_generation)!=int or claim_generation<1: raise ValueError("invalid_claim_generation")
  if not isinstance(claim_token,str) or not 20<=len(claim_token)<=256: raise PermissionError("invalid_claim_credential")
  digest=hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
  stamp=_stamp(_now())
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   row=db.execute("SELECT * FROM inference_jobs WHERE job_id=? AND device_id=?",(job_id,device.device_id)).fetchone()
   if row is None: raise LookupError("job_not_found")
   if row["status"]!="running" or not row["lease_expires_at"] or row["lease_expires_at"]<=stamp: raise ValueError("job_lease_expired_or_not_running")
   if row["claim_generation"]!=claim_generation or not row["claim_token_hash"] or not hmac.compare_digest(row["claim_token_hash"],digest): raise PermissionError("stale_or_invalid_claim")
   state="succeeded" if result_text is not None else "failed"
   changed=db.execute("UPDATE inference_jobs SET status=?,result_text=?,error_code=?,lease_expires_at=NULL,claim_token_hash=NULL,updated_at=? WHERE job_id=? AND status='running' AND claim_generation=? AND claim_token_hash=?",(state,result_text,error_code,stamp,job_id,claim_generation,digest)).rowcount
   if changed!=1: raise PermissionError("stale_or_invalid_claim")
   updated=db.execute("SELECT * FROM inference_jobs WHERE job_id=?",(job_id,)).fetchone()
  return self._job(updated)
 def renew_job_lease(self,device_token,job_id,*,claim_generation,claim_token):
  device=self._device(device_token)
  if not isinstance(job_id,str) or not re.fullmatch(r"[0-9a-f]{32}",job_id): raise ValueError("invalid_job_id")
  if type(claim_generation)!=int or claim_generation<1: raise ValueError("invalid_claim_generation")
  if not isinstance(claim_token,str) or not 20<=len(claim_token)<=256: raise PermissionError("invalid_claim_credential")
  digest=hashlib.sha256(claim_token.encode("utf-8")).hexdigest(); now=_now(); stamp=_stamp(now); expiry=_stamp(now+timedelta(seconds=self.lease_seconds))
  with self._connect() as db:
   db.execute("BEGIN IMMEDIATE")
   row=db.execute("SELECT status,lease_expires_at,claim_generation,claim_token_hash FROM inference_jobs WHERE job_id=? AND device_id=?",(job_id,device.device_id)).fetchone()
   if row is None: raise LookupError("job_not_found")
   if row["status"]!="running" or not row["lease_expires_at"] or row["lease_expires_at"]<=stamp: raise ValueError("job_lease_expired_or_not_running")
   if row["claim_generation"]!=claim_generation or not row["claim_token_hash"] or not hmac.compare_digest(row["claim_token_hash"],digest): raise PermissionError("stale_or_invalid_claim")
   changed=db.execute("UPDATE inference_jobs SET lease_expires_at=?,updated_at=? WHERE job_id=? AND status='running' AND claim_generation=? AND claim_token_hash=?",(expiry,stamp,job_id,claim_generation,digest)).rowcount
   if changed!=1: raise PermissionError("stale_or_invalid_claim")
   return expiry
 def cancel_job(self,owner_id,job_id):
  stamp=_stamp(_now())
  with self._connect() as db:
   changed=db.execute("UPDATE inference_jobs SET status='cancelled',lease_expires_at=NULL,claim_token_hash=NULL,updated_at=? WHERE owner_id=? AND job_id=? AND status IN ('queued','running')",(stamp,owner_id,job_id)).rowcount
   return changed==1
 def get_job(self,owner_id,job_id):
  with self._connect() as db: row=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
  return self._job(row) if row else None
 def list_jobs(self,owner_id,*,limit=100):
  if type(limit)!=int or not 1<=limit<=100: raise ValueError("invalid_limit")
  with self._connect() as db: rows=db.execute("SELECT * FROM inference_jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",(owner_id,limit)).fetchall()
  return [self._job(r) for r in rows]

class _Connection:
 def __init__(self, connection):
  self.connection=connection
 def __enter__(self):
  self.connection.row_factory=sqlite3.Row
  self.connection.execute("PRAGMA busy_timeout=5000")
  self.connection.execute("PRAGMA foreign_keys=ON")
  return self.connection
 def __exit__(self, exc_type, exc, tb):
  try:
   if exc_type is None: self.connection.commit()
   else: self.connection.rollback()
  finally:
   self.connection.close()
  return False
