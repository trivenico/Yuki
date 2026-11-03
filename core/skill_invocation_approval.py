"""Persistent one-shot approvals bound to an exact isolated skill invocation.

This module authorizes metadata only. It does not launch processes, import skills,
or make the current in-process skill runtime sandboxed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from contextlib import closing
from typing import Any, Mapping

from core.auth import BuiltInAuthService
from core.skill_execution_manifest import (
    SkillExecutionManifest, SkillManifestError, manifest_sha256,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9_.:@+-]{1,128}$")
_MAX_ARGUMENT_BYTES = 1_048_576
_MAX_GRANTS = 64


class InvocationApprovalError(PermissionError):
    """Raised when an invocation binding or approval is invalid."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvocationApprovalError("approval data must be finite JSON data") from exc


def _digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise InvocationApprovalError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvocationApprovalError(f"{field} must be a bounded identifier")
    return value


def _grants(values: Any, field: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > _MAX_GRANTS:
        raise InvocationApprovalError(f"{field} must contain at most 64 paths")
    if any(not isinstance(item, str) or not item or len(item) > 256
           or item.startswith("/") or "\\" in item or ":" in item
           or any(ord(char) < 32 or ord(char) == 127 for char in item)
           or any(part in {"", ".", ".."} for part in item.split("/"))
           for item in values):
        raise InvocationApprovalError(f"{field} contains an unsafe relative path")
    normalized = tuple(sorted(set(values)))
    if len(normalized) != len(values):
        raise InvocationApprovalError(f"{field} must be unique")
    return normalized


@dataclass(frozen=True)
class SkillInvocationBinding:
    actor_id: str
    tenant_id: str
    task_id: str
    invocation_id: str
    skill_id: str
    manifest_sha256: str
    artifact_sha256: str
    runtime_image: str
    entrypoint: str
    argument_sha256: str
    input_schema_sha256: str
    capabilities: tuple[str, ...]
    resource_limits: tuple[tuple[str, int], ...]
    staging_sha256: str
    read_grants: tuple[str, ...]
    write_grants: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "invocation_id": self.invocation_id,
            "skill_id": self.skill_id,
            "manifest_sha256": self.manifest_sha256,
            "artifact_sha256": self.artifact_sha256,
            "runtime_image": self.runtime_image,
            "entrypoint": self.entrypoint,
            "argument_sha256": self.argument_sha256,
            "input_schema_sha256": self.input_schema_sha256,
            "capabilities": list(self.capabilities),
            "resource_limits": dict(self.resource_limits),
            "staging_sha256": self.staging_sha256,
            "read_grants": list(self.read_grants),
            "write_grants": list(self.write_grants),
        }

    @property
    def binding_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.as_dict())).hexdigest()


def build_invocation_binding(
    manifest: SkillExecutionManifest,
    *,
    auth_service: BuiltInAuthService,
    actor_session_token: str,
    tenant_id: str,
    task_id: str,
    invocation_id: str,
    arguments: Any,
    staging_sha256: str,
    read_grants: tuple[str, ...] | list[str] = (),
    write_grants: tuple[str, ...] | list[str] = (),
) -> SkillInvocationBinding:
    """Bind identity, manifest, canonical arguments, staging and grants."""
    if not isinstance(manifest, SkillExecutionManifest):
        raise InvocationApprovalError("a validated skill manifest is required")
    if not isinstance(auth_service, BuiltInAuthService):
        raise InvocationApprovalError("trusted authentication service is required")
    actor = auth_service.resolve_session(actor_session_token)
    if actor is None:
        raise InvocationApprovalError("a valid authenticated actor session is required")
    try:
        argument_bytes = _canonical(arguments)
    except InvocationApprovalError:
        raise
    if len(argument_bytes) > _MAX_ARGUMENT_BYTES:
        raise InvocationApprovalError("canonical arguments exceed the 1 MiB limit")
    capabilities = manifest.capabilities
    reads, writes = _grants(read_grants, "read_grants"), _grants(write_grants, "write_grants")
    if writes and "write_staged_output" not in capabilities:
        raise InvocationApprovalError("write grants require write_staged_output capability")
    if reads and "read_staged_input" not in capabilities:
        raise InvocationApprovalError("read grants require read_staged_input capability")
    return SkillInvocationBinding(
        actor_id=_identifier(actor.user_id, "actor_id"),
        tenant_id=_identifier(tenant_id, "tenant_id"),
        task_id=_identifier(task_id, "task_id"),
        invocation_id=_identifier(invocation_id, "invocation_id"),
        skill_id=manifest.skill_id,
        manifest_sha256=manifest_sha256(manifest),
        artifact_sha256=_digest(manifest.artifact_sha256, "artifact_sha256"),
        runtime_image=manifest.runtime_image,
        entrypoint=manifest.entrypoint,
        argument_sha256=hashlib.sha256(argument_bytes).hexdigest(),
        input_schema_sha256=manifest.input_schema_sha256,
        capabilities=capabilities,
        resource_limits=manifest.resource_limits,
        staging_sha256=_digest(staging_sha256, "staging_sha256"),
        read_grants=reads,
        write_grants=writes,
    )


class OneShotInvocationApprovalStore:
    """SQLite-backed, atomic one-shot approval store; stores digests, never arguments."""

    def __init__(self, database: str | Path):
        if not isinstance(database, (str, Path)) or not str(database).strip():
            raise ValueError("database must be a non-empty path")
        self.database = str(Path(database).expanduser())
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS skill_invocation_approvals (
                    approval_id TEXT PRIMARY KEY,
                    binding_sha256 TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    approval_revision TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_skill_invocation_binding ON skill_invocation_approvals(binding_sha256)")
        if os.name != "nt":
            os.chmod(self.database, 0o600)

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def issue(
        self,
        binding: SkillInvocationBinding,
        *,
        auth_service: BuiltInAuthService,
        approver_session_token: str,
        approval_revision: str,
        ttl_seconds: int = 300,
    ) -> str:
        if not isinstance(binding, SkillInvocationBinding):
            raise InvocationApprovalError("validated invocation binding required")
        if not isinstance(auth_service, BuiltInAuthService):
            raise InvocationApprovalError("trusted authentication service is required")
        approver = auth_service.resolve_session(approver_session_token)
        if approver is None or "workflow:approve" not in approver.permissions:
            raise InvocationApprovalError("a valid approver session with workflow:approve permission is required")
        approver_id = _identifier(approver.user_id, "approved_by")
        _identifier(approval_revision, "approval_revision")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 3600:
            raise InvocationApprovalError("approval TTL must be between 1 and 3600 seconds")
        approval_id = secrets.token_urlsafe(32)
        now = _utcnow()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO skill_invocation_approvals VALUES(?,?,?,?,?,?,NULL)",
                (approval_id, binding.binding_sha256, approver_id, _iso(now + timedelta(seconds=ttl_seconds)),
                 approval_revision, _iso(now)),
            )
            db.commit()
        return approval_id

    def consume(
        self,
        approval_id: str,
        binding: SkillInvocationBinding,
        *,
        auth_service: BuiltInAuthService,
        actor_session_token: str,
        current_approval_revision: str,
    ) -> bool:
        """Revalidate the bound actor's live session, then atomically consume approval."""
        if (not isinstance(approval_id, str) or not 20 <= len(approval_id) <= 256
                or not isinstance(binding, SkillInvocationBinding)):
            return False
        if not isinstance(auth_service, BuiltInAuthService):
            return False
        try:
            _identifier(current_approval_revision, "current_approval_revision")
            actor = auth_service.resolve_session(actor_session_token)
        except (InvocationApprovalError, TypeError, ValueError):
            return False
        if actor is None or not secrets.compare_digest(actor.user_id, binding.actor_id):
            return False
        now = _iso(_utcnow())
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT binding_sha256, expires_at, approval_revision, consumed_at
                   FROM skill_invocation_approvals WHERE approval_id=?""",
                (approval_id,),
            ).fetchone()
            if (row is None or row[3] is not None or row[0] != binding.binding_sha256
                    or row[1] <= now or row[2] != current_approval_revision):
                db.rollback()
                return False
            updated = db.execute(
                """UPDATE skill_invocation_approvals SET consumed_at=?
                   WHERE approval_id=? AND consumed_at IS NULL""",
                (now, approval_id),
            )
            if updated.rowcount != 1:
                db.rollback()
                return False
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
