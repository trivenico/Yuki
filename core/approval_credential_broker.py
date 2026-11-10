"""Encrypted, worker-bound delivery for durable capability approval tokens.

Encryption is delegated to an injected KMS/envelope-encryption adapter. The
broker refuses plaintext storage and binds ciphertext with authenticated
associated data derived from the complete task identity.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from core.capability_approval_gateway import CapabilityApprovalGateway
from core.guarded_skill_runtime import SkillPolicyContext


class ApprovalCredentialBroker:
    """Deliver each approved credential once to one explicitly bound worker."""

    def __init__(
        self,
        database_path: str | Path,
        gateway: CapabilityApprovalGateway,
        *,
        worker_id: str,
        seal: Callable[[bytes, bytes], bytes],
        unseal: Callable[[bytes, bytes], bytes],
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path")
        if not isinstance(gateway, CapabilityApprovalGateway):
            raise TypeError("gateway must be a CapabilityApprovalGateway")
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", worker_id):
            raise ValueError("worker_id must be a bounded trusted identity")
        if not callable(seal) or not callable(unseal) or not callable(clock):
            raise TypeError("seal, unseal, and clock must be callable")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.gateway, self.worker_id = gateway, worker_id
        self.seal, self.unseal, self.clock = seal, unseal, clock
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS approval_worker_credentials (
                    request_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    credential_sha256 TEXT NOT NULL,
                    ciphertext BLOB NOT NULL,
                    expires_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('available','consumed','expired','revoked')),
                    created_at REAL NOT NULL,
                    consumed_at REAL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_worker_credentials_lookup
                ON approval_worker_credentials(principal_id,goal_id,task_id,worker_id,status)
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _aad(request_id: str, binding: dict[str, Any], worker_id: str) -> bytes:
        material = {
            "request_id": request_id, "principal_id": binding["principal_id"],
            "goal_id": binding["run_id"], "task_id": binding["step_id"],
            "worker_id": worker_id, "capability": binding["capability"],
            "target": binding["target"], "policy_version": binding["policy_version"],
            "expires_at": binding["expires_at"],
        }
        return json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def deliver_credential(self, request_id: str, token: str) -> None:
        """Approval-service callback: encrypt and persist without plaintext."""
        binding = self.gateway.approval_delivery_binding(request_id, token)
        token_bytes = token.encode("ascii")
        ciphertext = self.seal(token_bytes, self._aad(request_id, binding, self.worker_id))
        if not isinstance(ciphertext, bytes) or len(ciphertext) < 16 or len(ciphertext) > 16384:
            raise ValueError("encryption adapter returned an invalid envelope")
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """DELETE FROM approval_worker_credentials
                   WHERE status IN ('expired','revoked') OR expires_at<=?""", (now,),
            )
            try:
                db.execute(
                    """INSERT INTO approval_worker_credentials
                       (request_id,principal_id,goal_id,task_id,worker_id,
                        credential_sha256,ciphertext,expires_at,status,created_at)
                       VALUES(?,?,?,?,?,?,?,?, 'available',?)""",
                    (request_id, binding["principal_id"], binding["run_id"],
                     binding["step_id"], self.worker_id,
                     hashlib.sha256(token_bytes).hexdigest(), ciphertext,
                     binding["expires_at"], now),
                )
            except sqlite3.IntegrityError:
                db.rollback()
                raise PermissionError("credential delivery has already been recorded") from None
            db.commit()

    def resolver(self, worker_id: str | None = None) -> Callable[[SkillPolicyContext, str], str | None]:
        """Create a resolver closure pinned to this broker's worker identity."""
        if worker_id is not None and worker_id != self.worker_id:
            raise PermissionError("resolver worker identity does not match broker identity")

        def resolve(context: SkillPolicyContext, skill_name: str) -> str | None:
            if not isinstance(context, SkillPolicyContext):
                raise PermissionError("trusted task context is required")
            if not isinstance(skill_name, str) or not skill_name or len(skill_name) > 128:
                raise ValueError("skill_name is invalid")
            now = self.clock()
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute(
                    """SELECT * FROM approval_worker_credentials
                       WHERE principal_id=? AND goal_id=? AND task_id=? AND worker_id=?
                         AND status='available' AND expires_at>?
                       ORDER BY created_at DESC LIMIT 2""",
                    (context.principal_id, context.goal_id, context.task_id, self.worker_id, now),
                ).fetchall()
                if len(rows) != 1:
                    db.rollback()
                    return None
                row = rows[0]
                binding = self.gateway.approval_delivery_binding_by_digest(
                    row["request_id"], row["credential_sha256"],
                )
                aad = self._aad(row["request_id"], binding, self.worker_id)
                plaintext = self.unseal(bytes(row["ciphertext"]), aad)
                if not isinstance(plaintext, bytes) or not 40 <= len(plaintext) <= 256:
                    db.rollback()
                    raise PermissionError("credential envelope could not be verified")
                if not secrets.compare_digest(hashlib.sha256(plaintext).hexdigest(), row["credential_sha256"]):
                    db.rollback()
                    raise PermissionError("credential integrity verification failed")
                changed = db.execute(
                    """UPDATE approval_worker_credentials SET status='consumed',consumed_at=?
                       WHERE request_id=? AND status='available'""",
                    (now, row["request_id"]),
                ).rowcount
                if changed != 1:
                    db.rollback()
                    return None
                db.commit()
            try:
                return plaintext.decode("ascii")
            except UnicodeDecodeError:
                raise PermissionError("credential encoding is invalid") from None

        # The worker constructor verifies this pinned identity before accepting the resolver.
        resolve.worker_id = self.worker_id
        return resolve

    def revoke(self, request_id: str) -> bool:
        """Invalidate a not-yet-delivered credential record for this worker."""
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("request_id is invalid")
        with closing(self._connect()) as db:
            result = db.execute(
                """UPDATE approval_worker_credentials SET status='revoked',ciphertext=X''
                   WHERE request_id=? AND worker_id=? AND status='available'""",
                (request_id, self.worker_id),
            )
            return result.rowcount == 1
