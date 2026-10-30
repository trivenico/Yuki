"""Persistent resource-aware queue for approved local LoRA/QLoRA plans.

Queue metadata only: this module never downloads datasets, executes code, or trains weights.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
import sqlite3
from learning.dataset_registry import DatasetApproval, DatasetRegistry

class TrainingJobError(RuntimeError):
    pass

@dataclass(frozen=True)
class TrainingJob:
    job_id: str
    state: str
    dataset_repo: str
    dataset_revision: str
    base_model: str
    method: str
    requested_ram_gb: float
    requested_vram_gb: float
    max_runtime_minutes: int
    created_at: str
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    error_code: str | None

class TrainingJobQueue:
    def __init__(self, database_path, *, dataset_registry: DatasetRegistry,
                 max_ram_gb=64, max_vram_gb=80, max_runtime_minutes=1440):
        if not isinstance(dataset_registry, DatasetRegistry):
            raise TypeError("DatasetRegistry required")
        for name, value in (("RAM", max_ram_gb), ("VRAM", max_vram_gb)):
            if isinstance(value, bool) or not isinstance(value, (int,float)) or value <= 0:
                raise ValueError(f"Invalid {name} budget")
        if type(max_runtime_minutes) is not int or max_runtime_minutes < 1:
            raise ValueError("Invalid runtime budget")
        self.path=str(Path(database_path).expanduser())
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        self.registry=dataset_registry
        self.max_ram_gb=float(max_ram_gb); self.max_vram_gb=float(max_vram_gb)
        self.max_runtime_minutes=max_runtime_minutes
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS training_jobs (
              job_id TEXT PRIMARY KEY,state TEXT NOT NULL,dataset_repo TEXT NOT NULL,
              dataset_revision TEXT NOT NULL,base_model TEXT NOT NULL,method TEXT NOT NULL,
              requested_ram_gb REAL NOT NULL,requested_vram_gb REAL NOT NULL,
              max_runtime_minutes INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              lease_owner TEXT,lease_expires_at TEXT,error_code TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_training_queue ON training_jobs(state,created_at)")
    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=5); db.row_factory=sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db; db.commit()
        except Exception:
            db.rollback(); raise
        finally: db.close()
    @staticmethod
    def _row(row): return TrainingJob(**dict(row))
    def create(self, *, approval: DatasetApproval,base_model,method,requested_ram_gb,
               requested_vram_gb=0,max_runtime_minutes=60):
        approval.validate()
        if not self.registry.is_approved(approval.repo_id,approval.revision):
            raise PermissionError("Dataset revision is not approved")
        if not isinstance(base_model,str) or not base_model.strip() or len(base_model)>200:
            raise ValueError("Invalid base model")
        if method not in {"lora","qlora"}: raise ValueError("Only LoRA/QLoRA plans are allowed")
        for name,value,limit in (("RAM",requested_ram_gb,self.max_ram_gb),("VRAM",requested_vram_gb,self.max_vram_gb)):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or value<0 or value>limit:
                raise ValueError(f"{name} request exceeds budget")
        if requested_ram_gb<=0: raise ValueError("RAM reservation must be positive")
        if type(max_runtime_minutes) is not int or not 1<=max_runtime_minutes<=self.max_runtime_minutes:
            raise ValueError("Runtime request exceeds policy")
        now=datetime.now(timezone.utc).isoformat(); jid=secrets.token_urlsafe(18)
        with self._connect() as db:
            db.execute("INSERT INTO training_jobs VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
              (jid,approval.repo_id,approval.revision,base_model.strip(),method,float(requested_ram_gb),
               float(requested_vram_gb),max_runtime_minutes,now,now))
            return self._row(db.execute("SELECT * FROM training_jobs WHERE job_id=?",(jid,)).fetchone())
    def get(self,jid):
        with self._connect() as db:
            row=db.execute("SELECT * FROM training_jobs WHERE job_id=?",(jid,)).fetchone()
            return self._row(row) if row else None
    def _transition_queued(self,jid,old,new):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT * FROM training_jobs WHERE job_id=?",(jid,)).fetchone()
            if row is None: raise KeyError("Job not found")
            if row["state"]!=old: raise TrainingJobError(f"Job must be {old}")
            db.execute("UPDATE training_jobs SET state=?,updated_at=? WHERE job_id=?",
              (new,datetime.now(timezone.utc).isoformat(),jid))
            return self._row(db.execute("SELECT * FROM training_jobs WHERE job_id=?",(jid,)).fetchone())
    def pause(self,jid): return self._transition_queued(jid,"queued","paused")
    def resume(self,jid): return self._transition_queued(jid,"paused","queued")
    def cancel(self,jid): return self._transition_queued(jid,"queued","cancelled")
    def claim_next(self,*,worker_id,available_ram_gb,available_vram_gb=0,machine_idle=False,
                   on_external_power=False,allow_on_battery=False,lease_seconds=300):
        if not isinstance(worker_id,str) or not worker_id.strip() or len(worker_id)>128: raise ValueError("Invalid worker")
        for value in (available_ram_gb,available_vram_gb):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or value<0: raise ValueError("Invalid resources")
        if any(type(x) is not bool for x in (machine_idle,on_external_power,allow_on_battery)): raise ValueError("Invalid power flags")
        if type(lease_seconds) is not int or not 30<=lease_seconds<=3600: raise ValueError("Invalid lease")
        if not machine_idle or (not on_external_power and not allow_on_battery): return None
        now=datetime.now(timezone.utc)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("""SELECT * FROM training_jobs WHERE state='queued' AND requested_ram_gb<=?
              AND requested_vram_gb<=? ORDER BY created_at,job_id LIMIT 1""",(float(available_ram_gb),float(available_vram_gb))).fetchone()
            if row is None: return None
            expiry=(now+timedelta(seconds=lease_seconds)).isoformat()
            db.execute("UPDATE training_jobs SET state='running',lease_owner=?,lease_expires_at=?,updated_at=? WHERE job_id=? AND state='queued'",
              (worker_id,expiry,now.isoformat(),row["job_id"]))
            return self._row(db.execute("SELECT * FROM training_jobs WHERE job_id=?",(row["job_id"],)).fetchone())
    def finish(self,jid,*,worker_id,success,error_code=None):
        if not isinstance(worker_id,str) or not worker_id.strip() or type(success) is not bool: raise ValueError("Invalid finish request")
        if error_code is not None and (not isinstance(error_code,str) or len(error_code)>100): raise ValueError("Invalid error code")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT * FROM training_jobs WHERE job_id=?",(jid,)).fetchone()
            if row is None: raise KeyError("Job not found")
            if row["state"]!="running" or row["lease_owner"]!=worker_id: raise TrainingJobError("Worker does not own lease")
            state="succeeded" if success else "failed"
            db.execute("UPDATE training_jobs SET state=?,error_code=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE job_id=?",
              (state,error_code,datetime.now(timezone.utc).isoformat(),jid))
            return self._row(db.execute("SELECT * FROM training_jobs WHERE job_id=?",(jid,)).fetchone())
