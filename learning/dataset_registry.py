"""Curated dataset catalogue with optional durable, HMAC-authenticated approvals.

Discovery is metadata-only. Approval records never trigger downloads or training.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_LICENSE = re.compile(r"^[A-Za-z0-9.+-]{2,80}$")
_ALLOWED_LICENSES = frozenset({"Apache-2.0","MIT","BSD-2-Clause","BSD-3-Clause","CC0-1.0","CC-BY-4.0"})
_STARTER_CANDIDATES = (
 {"repo_id":"OpenAssistant/oasst1","tasks":["instruction_following","dialogue"],"notes":"Inspect privacy, provenance and exact terms."},
 {"repo_id":"openai/gsm8k","tasks":["math_reasoning"],"notes":"Verify license and isolate benchmark evaluation splits."},
 {"repo_id":"google-research-datasets/mbpp","tasks":["code_generation"],"notes":"Verify terms and contamination risk."},
 {"repo_id":"truthfulqa/truthful_qa","tasks":["truthfulness","evaluation"],"notes":"Verify task-specific terms and permitted use."},
 {"repo_id":"HuggingFaceH4/ultrafeedback_binarized","tasks":["preference_learning"],"notes":"Audit upstream source lineage and component licenses."},
)

@dataclass(frozen=True)
class DatasetApproval:
    repo_id: str
    revision: str
    license_id: str
    license_evidence_url: str
    provenance_reviewed: bool
    privacy_reviewed: bool
    security_reviewed: bool
    human_approved: bool
    approved_by: str

    def validate(self) -> None:
        if not isinstance(self.repo_id,str) or not _REPO_ID.fullmatch(self.repo_id):
            raise ValueError("repo_id must be an exact owner/name identifier.")
        if not isinstance(self.revision,str) or not _REVISION.fullmatch(self.revision):
            raise ValueError("A pinned 40-character commit revision is required.")
        if not isinstance(self.license_id,str) or not _LICENSE.fullmatch(self.license_id):
            raise ValueError("A valid SPDX-style license identifier is required.")
        if self.license_id not in _ALLOWED_LICENSES:
            raise PermissionError("License is not in the initial allowlist; manual legal review is required.")
        if not isinstance(self.license_evidence_url,str) or not self.license_evidence_url.startswith("https://huggingface.co/datasets/"):
            raise ValueError("An HTTPS dataset-card evidence URL is required.")
        if not all(type(v) is bool and v for v in (self.provenance_reviewed,self.privacy_reviewed,self.security_reviewed,self.human_approved)):
            raise PermissionError("Provenance, privacy, security and human approval gates must all pass.")
        if not isinstance(self.approved_by,str) or not self.approved_by.strip() or len(self.approved_by)>128:
            raise ValueError("A named reviewer is required.")

    def to_dict(self):
        self.validate()
        return asdict(self)

class DatasetRegistry:
    """Dataset discovery plus in-memory or signed persistent approval records.

    For durable mode supply database_path and a secret signing_key of at least
    32 bytes. Store that key outside the database (OS keychain/secret manager).
    """
    def __init__(self,candidates: Iterable[dict]=_STARTER_CANDIDATES,*,database_path=None,signing_key=None):
        self._candidates=tuple(dict(x) for x in candidates)
        self._approvals={}
        self.path=str(Path(database_path).expanduser()) if database_path is not None else None
        self._key=signing_key.encode("utf-8") if isinstance(signing_key,str) else signing_key
        if self.path is not None:
            if not isinstance(self._key,bytes) or len(self._key)<32:
                raise ValueError("Persistent approvals require a signing key of at least 32 bytes.")
            Path(self.path).parent.mkdir(parents=True,exist_ok=True)
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS dataset_approvals (
                    repo_id TEXT NOT NULL, revision TEXT NOT NULL, approval_json TEXT NOT NULL,
                    signature TEXT NOT NULL, PRIMARY KEY(repo_id,revision))""")
            self._verify_all()

    def _connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _canonical(record):
        return json.dumps(record,sort_keys=True,separators=(",",":"),ensure_ascii=False)

    def _signature(self,record):
        return hmac.new(self._key,self._canonical(record).encode("utf-8"),hashlib.sha256).hexdigest()

    def _verify_all(self):
        with self._connect() as db:
            rows=db.execute("SELECT approval_json,signature FROM dataset_approvals").fetchall()
        for raw,signature in rows:
            try:
                record=json.loads(raw)
                if not hmac.compare_digest(self._signature(record),signature):
                    raise ValueError
                DatasetApproval(**record).validate()
            except Exception as exc:
                raise RuntimeError("Dataset approval store integrity check failed; refusing approvals.") from exc

    def candidates(self):
        return [{**x,"source":"Hugging Face Datasets",
                 "status":"candidate_only_license_and_safety_review_required",
                 "automatic_download":False,"training_eligible":False} for x in self._candidates]

    def approve(self,approval: DatasetApproval):
        if not isinstance(approval,DatasetApproval): raise TypeError("approval must be DatasetApproval")
        record=approval.to_dict()
        if not any(x["repo_id"]==approval.repo_id for x in self._candidates):
            raise ValueError("Dataset is not in the reviewed candidate catalogue.")
        if self.path is None:
            self._approvals[(approval.repo_id,approval.revision)]=approval
        else:
            raw=self._canonical(record); signature=self._signature(record)
            with self._connect() as db:
                db.execute("INSERT INTO dataset_approvals VALUES (?,?,?,?) "
                           "ON CONFLICT(repo_id,revision) DO UPDATE SET approval_json=excluded.approval_json,signature=excluded.signature",
                           (approval.repo_id,approval.revision,raw,signature))
        return {"repo_id":approval.repo_id,"revision":approval.revision,
                "status":"approved_for_separate_ingestion_review",
                "training_eligible":True,"automatic_download":False}

    def is_approved(self,repo_id,revision):
        if self.path is None: return (repo_id,revision) in self._approvals
        with self._connect() as db:
            row=db.execute("SELECT approval_json,signature FROM dataset_approvals WHERE repo_id=? AND revision=?",
                           (repo_id,revision)).fetchone()
        if row is None: return False
        try:
            record=json.loads(row[0])
            if not hmac.compare_digest(self._signature(record),row[1]): return False
            approval=DatasetApproval(**record); approval.validate()
            return approval.repo_id==repo_id and approval.revision==revision
        except Exception:
            return False

    def approved_records(self):
        if self.path is None:
            return [x.to_dict() for x in self._approvals.values()]
        with self._connect() as db:
            rows=db.execute("SELECT approval_json,signature FROM dataset_approvals ORDER BY repo_id,revision").fetchall()
        records=[]
        for raw,signature in rows:
            record=json.loads(raw)
            if not hmac.compare_digest(self._signature(record),signature):
                raise RuntimeError("Dataset approval store integrity check failed.")
            records.append(DatasetApproval(**record).to_dict())
        return records
