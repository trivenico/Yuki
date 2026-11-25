"""PostgreSQL-backed workload delegation with transaction-bound verification.

A protected workflow mutation supplies its already-open PostgreSQL connection to
verify_in_transaction(); the delegation chain check, verification audit event,
workflow state change, and task event therefore commit or roll back together.
"""
from __future__ import annotations

from core.postgres_database_identity import postgres_database_identity

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import time
from typing import Iterable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

_TEXT = re.compile(r"^[^\x00\r\n]{1,256}$")
_SCOPE = re.compile(r"^[^\x00\r\n]{1,128}$")
_MAX_DEPTH = 8


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


class PostgresDelegationAuthority:
    """Durable revocable grants shared by all worker replicas."""

    def __init__(self, database_url: str, *, min_pool_size: int = 1,
                 max_pool_size: int = 10, pool_timeout: float = 10.0):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("delegation_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("delegation_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("delegation_pool_timeout_invalid")
        self.database_url = database_url
        self.database_identity = postgres_database_identity(database_url)
        self.pool = ConnectionPool(conninfo=database_url, min_size=min_pool_size,
            max_size=max_pool_size, timeout=float(pool_timeout),
            kwargs={"row_factory": dict_row}, open=True)
        try:
            self.pool.wait(timeout=float(pool_timeout))
            self.check_ready()
        except Exception:
            self.pool.close()
            raise

    @staticmethod
    def _required(value, label):
        if not isinstance(value, str) or not _TEXT.fullmatch(value) or value != value.strip():
            raise ValueError(label + " must be bounded, trimmed text")
        return value

    @staticmethod
    def _scopes(scopes, label="scopes"):
        if isinstance(scopes, (str, bytes)):
            raise ValueError(label + " must be a collection of scope identifiers")
        values = frozenset(scopes)
        if not 1 <= len(values) <= 64 or any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in values):
            raise ValueError(label + " must contain 1-64 bounded identifiers")
        return values

    @staticmethod
    def _grant(row):
        scopes = row["scopes_json"]
        if isinstance(scopes, str):
            scopes = json.loads(scopes)
        return DelegationGrant(
            delegation_id=row["delegation_id"], grantor_id=row["grantor_id"],
            grantee_id=row["grantee_id"], tenant_id=row["tenant_id"],
            agent_id=row["agent_id"], task_id=row["task_id"],
            lease_generation=row["lease_generation"], scopes=frozenset(scopes),
            issued_at=int(row["issued_at"]), expires_at=int(row["expires_at"]),
            parent_id=row["parent_id"],
        )

    @staticmethod
    def _event(db, event_type, grant, actor_id, now):
        db.execute(
            "INSERT INTO workload_delegation_events "
            "(event_type,delegation_id,actor_id,task_id,scopes_json,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (event_type, grant.delegation_id, actor_id, grant.task_id,
             Jsonb(sorted(grant.scopes)), now),
        )

    def create_grant(self, *, grantor_id: str, grantee_id: str, tenant_id: str,
                     agent_id: str, task_id: str, lease_generation: str,
                     scopes: Iterable[str], expires_at: int, parent_id: str | None = None,
                     now: int | None = None) -> DelegationGrant:
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
        scope_set = self._scopes(scopes)
        delegation_id = secrets.token_urlsafe(24)
        with self.pool.connection() as db:
            with db.transaction():
                parent = None
                if parent_id is not None:
                    parent = self.verify_in_transaction(
                        db, parent_id, grantee_id=grantor, tenant_id=tenant,
                        agent_id=agent, task_id=task, lease_generation=generation,
                        scopes=scope_set, now=current,
                    )
                    if not scope_set.issubset(parent.scopes) or expires_at > parent.expires_at:
                        raise PermissionError("child delegation exceeds parent authority")
                grant = DelegationGrant(delegation_id, grantor, grantee, tenant,
                    agent, task, generation, scope_set, current, expires_at, parent_id)
                db.execute(
                    "INSERT INTO workload_delegations "
                    "(delegation_id,grantor_id,grantee_id,tenant_id,agent_id,task_id,"
                    "lease_generation,scopes_json,issued_at,expires_at,parent_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (delegation_id, grantor, grantee, tenant, agent, task, generation,
                     Jsonb(sorted(scope_set)), current, expires_at, parent_id),
                )
                self._event(db, "issued", grant, grantor, current)
                return grant

    def verify_in_transaction(self, db, delegation_id: str, *, grantee_id: str,
                              tenant_id: str, agent_id: str, task_id: str,
                              lease_generation: str, scopes: Iterable[str],
                              now: int) -> DelegationGrant:
        identifier = self._required(delegation_id, "delegation_id")
        grantee = self._required(grantee_id, "grantee_id")
        tenant = self._required(tenant_id, "tenant_id")
        agent = self._required(agent_id, "agent_id")
        task = self._required(task_id, "task_id")
        generation = self._required(lease_generation, "lease_generation")
        if type(now) is not int or now < 0:
            raise ValueError("invalid delegation verification timestamp")
        requested = self._scopes(scopes, "requested scopes")
        # The authoritative expiry decision must use the database clock, not
        # the caller clock captured during credential preflight.
        db_now = int(db.execute("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint AS now").fetchone()["now"])
        chain, seen, cursor = [], set(), identifier
        while cursor is not None:
            if cursor in seen or len(chain) >= _MAX_DEPTH:
                raise PermissionError("delegation chain is invalid")
            seen.add(cursor)
            row = db.execute(
                "SELECT * FROM workload_delegations WHERE delegation_id=%s FOR UPDATE",
                (cursor,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or row["issued_at"] > db_now or row["expires_at"] <= db_now:
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
        self._event(db, "verified", leaf, grantee, db_now)
        return leaf

    def verify(self, delegation_id: str | None, *, grantee_id: str, tenant_id: str,
               agent_id: str, task_id: str, lease_generation: str,
               scopes: Iterable[str], now: int | None = None) -> DelegationGrant:
        if delegation_id is None:
            raise PermissionError("a trusted delegation is required")
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0:
            raise ValueError("now must be an integer epoch timestamp")
        with self.pool.connection() as db:
            with db.transaction():
                return self.verify_in_transaction(
                    db, delegation_id, grantee_id=grantee_id, tenant_id=tenant_id,
                    agent_id=agent_id, task_id=task_id,
                    lease_generation=lease_generation, scopes=scopes, now=current,
                )

    def revoke(self, delegation_id: str, *, revoked_by: str, now: int | None = None) -> bool:
        identifier = self._required(delegation_id, "delegation_id")
        actor = self._required(revoked_by, "revoked_by")
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0:
            raise ValueError("now must be an integer epoch timestamp")
        with self.pool.connection() as db:
            with db.transaction():
                row = db.execute(
                    "SELECT * FROM workload_delegations WHERE delegation_id=%s FOR UPDATE",
                    (identifier,),
                ).fetchone()
                if row is None:
                    return False
                if actor not in (row["grantor_id"], row["grantee_id"]):
                    raise PermissionError("only the grantor or grantee may revoke this delegation")
                if row["revoked_at"] is not None:
                    return False
                db.execute("UPDATE workload_delegations SET revoked_at=%s WHERE delegation_id=%s",
                           (current, identifier))
                self._event(db, "revoked", self._grant(row), actor, current)
                return True

    def check_ready(self):
        with self.pool.connection() as db:
            db.execute("SELECT delegation_id,parent_id,revoked_at FROM workload_delegations LIMIT 0")
            db.execute("SELECT event_id,event_type,delegation_id FROM workload_delegation_events LIMIT 0")
        return True

    def close(self):
        self.pool.close()
