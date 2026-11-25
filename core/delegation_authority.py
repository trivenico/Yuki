"""Authoritative, durable delegation grants for Yuki workload execution.

Grant creation is a trusted host operation, never a model/tool operation.
Every verification walks the parent chain so parent revocation or expiry
invalidates descendants immediately.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

_TEXT = re.compile(r"^[^\x00\r\n]{1,256}$")
_SCOPE = re.compile(r"^[^\x00\r\n]{1,128}$")
_MAX_DEPTH = 8


@runtime_checkable
class DelegationAuthorityProtocol(Protocol):
    database_identity: str
    def create_grant(self, **kwargs): ...
    def verify(self, delegation_id, **kwargs): ...
    def verify_in_transaction(self, db, delegation_id, **kwargs): ...
    def revoke(self, delegation_id, **kwargs): ...


@dataclass(frozen=True)
class DelegationGrant:
    delegation_id: str
    grantor_id: str
    grantee_id: str
    tenant_id: str
    agent_id: str
    task_id: str
    lease_generation: str
    scopes: frozenset[str]
    issued_at: int
    expires_at: int
    parent_id: str | None = None


class DelegationAuthority:
    """SQLite-backed source of truth for narrow, revocable delegation grants."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be non-empty text")
        self.database_path = database_path
        self.database_identity = "sqlite:" + str(Path(database_path).resolve())
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS workload_delegations (
                    delegation_id TEXT PRIMARY KEY,
                    grantor_id TEXT NOT NULL,
                    grantee_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    lease_generation TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    parent_id TEXT,
                    revoked_at INTEGER
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(workload_delegations)")}
            if "lease_generation" not in columns:
                # Legacy grants cannot be bound retroactively; NULL means invalid.
                db.execute("ALTER TABLE workload_delegations ADD COLUMN lease_generation TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_workload_delegations_parent ON workload_delegations(parent_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS workload_delegation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    delegation_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_workload_delegation_events_grant ON workload_delegation_events(delegation_id, event_id)")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _insert_event(db, event_type: str, grant: DelegationGrant, actor_id: str, now: int) -> None:
        db.execute(
            """INSERT INTO workload_delegation_events
               (event_type, delegation_id, actor_id, task_id, scopes_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_type, grant.delegation_id, actor_id, grant.task_id,
             json.dumps(sorted(grant.scopes)), now),
        )

    def _record_event(self, event_type: str, grant: DelegationGrant, actor_id: str, now: int) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_event(db, event_type, grant, actor_id, now)
            db.commit()

    @staticmethod
    def _required(value: object, label: str) -> str:
        if not isinstance(value, str) or not _TEXT.fullmatch(value) or value != value.strip():
            raise ValueError(label + " must be bounded, trimmed text")
        return value

    @staticmethod
    def _grant(row) -> DelegationGrant:
        return DelegationGrant(
            delegation_id=row["delegation_id"], grantor_id=row["grantor_id"],
            grantee_id=row["grantee_id"], tenant_id=row["tenant_id"],
            agent_id=row["agent_id"], task_id=row["task_id"],
            lease_generation=row["lease_generation"],
            scopes=frozenset(json.loads(row["scopes_json"])),
            issued_at=row["issued_at"], expires_at=row["expires_at"],
            parent_id=row["parent_id"],
        )

    def create_grant(
        self, *, grantor_id: str, grantee_id: str, tenant_id: str,
        agent_id: str, task_id: str, lease_generation: str,
        scopes: Iterable[str], expires_at: int, parent_id: str | None = None,
        now: int | None = None,
    ) -> DelegationGrant:
        """Create a grant from a trusted host after authenticating grantor_id."""
        grantor = self._required(grantor_id, "grantor_id")
        grantee = self._required(grantee_id, "grantee_id")
        tenant = self._required(tenant_id, "tenant_id")
        agent = self._required(agent_id, "agent_id")
        task = self._required(task_id, "task_id")
        generation = self._required(lease_generation, "lease_generation")
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0 or type(expires_at) is not int:
            raise ValueError("grant timestamps must be integer epoch seconds")
        if not current < expires_at <= current + 900:
            raise ValueError("delegation lifetime must be between 1 and 900 seconds")
        if isinstance(scopes, (str, bytes)):
            raise ValueError("scopes must be a collection of scope identifiers")
        scope_set = frozenset(scopes)
        if not 1 <= len(scope_set) <= 64 or any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in scope_set):
            raise ValueError("delegation scopes must contain 1-64 bounded identifiers")
        parent = self.verify(parent_id, grantee_id=grantor, tenant_id=tenant,
                             agent_id=agent, task_id=task, lease_generation=generation,
                             scopes=scope_set, now=current) if parent_id is not None else None
        if parent is not None and (not scope_set.issubset(parent.scopes) or expires_at > parent.expires_at):
            raise PermissionError("child delegation exceeds parent authority")
        delegation_id = secrets.token_urlsafe(24)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if parent_id is not None:
                cursor = parent_id
                depth = 0
                while cursor is not None:
                    row = db.execute(
                        "SELECT delegation_id, parent_id, revoked_at, expires_at "
                        "FROM workload_delegations WHERE delegation_id=?",
                        (cursor,),
                    ).fetchone()
                    if row is None or row["revoked_at"] is not None or row["expires_at"] <= current:
                        db.rollback()
                        raise PermissionError("parent delegation is no longer active")
                    depth += 1
                    if depth >= _MAX_DEPTH:
                        db.rollback()
                        raise PermissionError("delegation chain exceeds maximum depth")
                    cursor = row["parent_id"]
            db.execute("""
                INSERT INTO workload_delegations
                (delegation_id,grantor_id,grantee_id,tenant_id,agent_id,task_id,lease_generation,scopes_json,issued_at,expires_at,parent_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (delegation_id,grantor,grantee,tenant,agent,task,generation,json.dumps(sorted(scope_set)),current,expires_at,parent_id))
            grant = DelegationGrant(delegation_id,grantor,grantee,tenant,agent,task,generation,scope_set,current,expires_at,parent_id)
            self._insert_event(db, "issued", grant, grantor, current)
            db.commit()
        return grant

    def verify_in_transaction(
        self, db, delegation_id: str, *, grantee_id: str, tenant_id: str,
        agent_id: str, task_id: str, lease_generation: str,
        scopes: Iterable[str], now: int,
    ) -> DelegationGrant:
        """Verify and audit a grant using the caller's already-open SQLite transaction.

        The caller must use the same database file and hold BEGIN IMMEDIATE through
        its protected mutation. Revocation then serializes before or after that commit.
        """
        identifier = self._required(delegation_id, "delegation_id")
        grantee = self._required(grantee_id, "grantee_id")
        tenant = self._required(tenant_id, "tenant_id")
        agent = self._required(agent_id, "agent_id")
        task = self._required(task_id, "task_id")
        generation = self._required(lease_generation, "lease_generation")
        if type(now) is not int or now < 0 or isinstance(scopes, (str, bytes)):
            raise ValueError("invalid delegation verification context")
        requested = frozenset(scopes)
        if not 1 <= len(requested) <= 64 or any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in requested):
            raise ValueError("requested scopes must contain 1-64 bounded identifiers")
        chain = []
        seen = set()
        cursor = identifier
        while cursor is not None:
            if cursor in seen or len(chain) >= _MAX_DEPTH:
                raise PermissionError("delegation chain is invalid")
            seen.add(cursor)
            row = db.execute("SELECT * FROM workload_delegations WHERE delegation_id=?", (cursor,)).fetchone()
            if row is None or row["revoked_at"] is not None or row["issued_at"] > now or row["expires_at"] <= now:
                raise PermissionError("delegation is missing, expired, or revoked")
            grant = self._grant(row)
            if (grant.tenant_id, grant.agent_id, grant.task_id, grant.lease_generation) != (tenant, agent, task, generation):
                raise PermissionError("delegation context or task lease generation does not match workload")
            chain.append(grant)
            cursor = grant.parent_id
        leaf = chain[0]
        if leaf.grantee_id != grantee or not requested.issubset(leaf.scopes):
            raise PermissionError("delegation does not grant the requested workload scope")
        for child, parent in zip(chain, chain[1:]):
            if (child.grantor_id != parent.grantee_id or child.lease_generation != parent.lease_generation
                    or not child.scopes.issubset(parent.scopes) or child.expires_at > parent.expires_at):
                raise PermissionError("delegation chain exceeds parent authority")
        self._insert_event(db, "verified", leaf, grantee, now)
        return leaf

    def verify(
        self, delegation_id: str | None, *, grantee_id: str, tenant_id: str,
        agent_id: str, task_id: str, lease_generation: str,
        scopes: Iterable[str], now: int | None = None,
    ) -> DelegationGrant:
        """Verify active grant and complete ancestor chain for one exact task."""
        if delegation_id is None:
            raise PermissionError("a trusted delegation is required")
        identifier = self._required(delegation_id, "delegation_id")
        grantee = self._required(grantee_id, "grantee_id")
        tenant = self._required(tenant_id, "tenant_id")
        agent = self._required(agent_id, "agent_id")
        task = self._required(task_id, "task_id")
        generation = self._required(lease_generation, "lease_generation")
        if isinstance(scopes, (str, bytes)):
            raise ValueError("scopes must be a collection of scope identifiers")
        requested = frozenset(scopes)
        if not 1 <= len(requested) <= 64 or any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in requested):
            raise ValueError("requested scopes must contain 1-64 bounded identifiers")
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0:
            raise ValueError("now must be an integer epoch timestamp")
        with self._connect() as db:
            chain = []
            seen = set()
            cursor = identifier
            while cursor is not None:
                if cursor in seen or len(chain) >= _MAX_DEPTH:
                    raise PermissionError("delegation chain is invalid")
                seen.add(cursor)
                row = db.execute("SELECT * FROM workload_delegations WHERE delegation_id=?", (cursor,)).fetchone()
                if row is None or row["revoked_at"] is not None or row["issued_at"] > current or row["expires_at"] <= current:
                    raise PermissionError("delegation is missing, expired, or revoked")
                grant = self._grant(row)
                if (grant.tenant_id, grant.agent_id, grant.task_id, grant.lease_generation) != (tenant, agent, task, generation):
                    raise PermissionError("delegation context or task lease generation does not match workload")
                chain.append(grant)
                cursor = grant.parent_id
        leaf = chain[0]
        if leaf.grantee_id != grantee or not requested.issubset(leaf.scopes):
            raise PermissionError("delegation does not grant the requested workload scope")
        for child, parent in zip(chain, chain[1:]):
            if (child.grantor_id != parent.grantee_id
                    or child.lease_generation != parent.lease_generation
                    or not child.scopes.issubset(parent.scopes)
                    or child.expires_at > parent.expires_at):
                raise PermissionError("delegation chain exceeds parent authority")
        self._record_event("verified", leaf, grantee, current)
        return leaf

    def revoke(self, delegation_id: str, *, revoked_by: str, now: int | None = None) -> bool:
        identifier = self._required(delegation_id, "delegation_id")
        actor = self._required(revoked_by, "revoked_by")
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0:
            raise ValueError("now must be an integer epoch timestamp")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT grantor_id, grantee_id, revoked_at FROM workload_delegations WHERE delegation_id=?", (identifier,)).fetchone()
            if row is None:
                db.rollback()
                return False
            if actor not in (row["grantor_id"], row["grantee_id"]):
                db.rollback()
                raise PermissionError("only the grantor or grantee may revoke this delegation")
            if row["revoked_at"] is not None:
                db.commit()
                return False
            db.execute("UPDATE workload_delegations SET revoked_at=? WHERE delegation_id=?", (current, identifier))
            grant_row = db.execute("SELECT * FROM workload_delegations WHERE delegation_id=?", (identifier,)).fetchone()
            self._insert_event(db, "revoked", self._grant(grant_row), actor, current)
            db.commit()
            return True
