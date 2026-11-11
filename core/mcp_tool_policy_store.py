"""Durable SQLite storage for host-reviewed MCP tool policies.

Policy state is owner/provider/account scoped. Every mutation advances a
monotonic revision in the same transaction as the policy change, allowing
existing immutable registries to fail closed immediately after revocation.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

from core.sqlite_file_security import prepare_private_sqlite_path
import re
import sqlite3

from core.mcp_tool_policy import (
    MCPToolPolicy,
    MCPToolPolicyError,
    MCPToolPolicyRegistry,
    MCPToolRisk,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")
_MAX_POLICIES_PER_ACCOUNT = 1000


class MCPToolPolicyStoreError(RuntimeError):
    """Stable durable policy-store failure."""


class MCPToolPolicyConflict(MCPToolPolicyStoreError):
    """The caller's expected registry revision is stale."""


class SQLiteMCPToolPolicyStore:
    """Persistent policy store with atomic compare-and-swap updates.

    A scope revision starts at 1, including when no policies exist. Every
    successful set/remove increments it exactly once. The revision row is
    retained when the final policy is removed, so old clients cannot become
    valid again after a delete/recreate cycle.
    """

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS mcp_tool_policy_revisions (
                owner_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                PRIMARY KEY(owner_id, provider_id, account_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS mcp_tool_policies (
                owner_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                risk TEXT NOT NULL,
                required_scopes_json TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version >= 1),
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                PRIMARY KEY(owner_id, provider_id, account_id, tool_name)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_mcp_tool_policies_scope
                ON mcp_tool_policies(owner_id, provider_id, account_id, tool_name)""")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.database_path, timeout=10.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _identity(value, label):
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid_" + label)
        return value

    @classmethod
    def _scope_identity(cls, owner_id, provider_id, account_id):
        return (
            cls._identity(owner_id, "owner_id"),
            cls._identity(provider_id, "provider_id"),
            cls._identity(account_id, "account_id"),
        )

    @staticmethod
    def _validate_policy(policy, owner_id, provider_id, account_id):
        if not isinstance(policy, MCPToolPolicy):
            raise ValueError("trusted_mcp_tool_policy_required")
        if (policy.owner_id, policy.provider_id, policy.account_id) != (
            owner_id, provider_id, account_id
        ):
            raise ValueError("mcp_tool_policy_scope_mismatch")
        if not _TOOL_NAME.fullmatch(policy.tool_name):
            raise ValueError("invalid_tool_name")
        if not isinstance(policy.risk, MCPToolRisk):
            raise ValueError("invalid_tool_risk")
        if not isinstance(policy.required_scopes, tuple) or len(policy.required_scopes) > 64:
            raise ValueError("invalid_required_scopes")
        if any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in policy.required_scopes):
            raise ValueError("invalid_required_scopes")
        if len(set(policy.required_scopes)) != len(policy.required_scopes):
            raise ValueError("duplicate_required_scopes")
        if type(policy.version) is not int or policy.version < 1 or type(policy.enabled) is not bool:
            raise ValueError("invalid_policy_version_or_enabled")

    @staticmethod
    def _begin_scope(db, owner_id, provider_id, account_id):
        db.execute("""INSERT OR IGNORE INTO mcp_tool_policy_revisions
            (owner_id, provider_id, account_id, revision) VALUES (?, ?, ?, 1)""",
            (owner_id, provider_id, account_id))
        row = db.execute("""SELECT revision FROM mcp_tool_policy_revisions
            WHERE owner_id=? AND provider_id=? AND account_id=?""",
            (owner_id, provider_id, account_id)).fetchone()
        if row is None or type(row["revision"]) is not int or row["revision"] < 1:
            raise MCPToolPolicyStoreError("policy_revision_corrupt")
        return row["revision"]

    @staticmethod
    def _check_expected(expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("invalid_expected_revision")

    def current_revision(self, *, owner_id, provider_id, account_id) -> int:
        owner, provider, account = self._scope_identity(owner_id, provider_id, account_id)
        with self._connection() as db:
            row = db.execute("""SELECT revision FROM mcp_tool_policy_revisions
                WHERE owner_id=? AND provider_id=? AND account_id=?""",
                (owner, provider, account)).fetchone()
        if row is None:
            return 1
        revision = row["revision"]
        if type(revision) is not int or revision < 1:
            raise MCPToolPolicyStoreError("policy_revision_corrupt")
        return revision

    def set_policy(
        self, *, owner_id, provider_id, account_id, policy: MCPToolPolicy,
        expected_revision: int,
    ) -> int:
        owner, provider, account = self._scope_identity(owner_id, provider_id, account_id)
        self._validate_policy(policy, owner, provider, account)
        self._check_expected(expected_revision)
        scopes_json = json.dumps(
            list(policy.required_scopes), ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                revision = self._begin_scope(db, owner, provider, account)
                if revision != expected_revision:
                    raise MCPToolPolicyConflict("stale_policy_revision")
                existing = db.execute("""SELECT version FROM mcp_tool_policies
                    WHERE owner_id=? AND provider_id=? AND account_id=? AND tool_name=?""",
                    (owner, provider, account, policy.tool_name)).fetchone()
                expected_version = (existing["version"] + 1) if existing else 1
                if policy.version != expected_version:
                    raise MCPToolPolicyConflict("stale_policy_version")
                if existing is None:
                    count = db.execute("""SELECT COUNT(*) FROM mcp_tool_policies
                        WHERE owner_id=? AND provider_id=? AND account_id=?""",
                        (owner, provider, account)).fetchone()[0]
                    if count >= _MAX_POLICIES_PER_ACCOUNT:
                        raise MCPToolPolicyStoreError("policy_capacity_reached")
                    db.execute("""INSERT INTO mcp_tool_policies
                        (owner_id, provider_id, account_id, tool_name, risk,
                         required_scopes_json, version, enabled)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (owner, provider, account, policy.tool_name, policy.risk.value,
                         scopes_json, policy.version, int(policy.enabled)))
                else:
                    db.execute("""UPDATE mcp_tool_policies SET risk=?, required_scopes_json=?,
                        version=?, enabled=? WHERE owner_id=? AND provider_id=?
                        AND account_id=? AND tool_name=?""",
                        (policy.risk.value, scopes_json, policy.version, int(policy.enabled),
                         owner, provider, account, policy.tool_name))
                next_revision = revision + 1
                db.execute("""UPDATE mcp_tool_policy_revisions SET revision=?
                    WHERE owner_id=? AND provider_id=? AND account_id=? AND revision=?""",
                    (next_revision, owner, provider, account, revision))
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise MCPToolPolicyConflict("stale_policy_revision")
                db.execute("COMMIT")
                return next_revision
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def remove_policy(
        self, *, owner_id, provider_id, account_id, tool_name: str,
        expected_revision: int,
    ) -> int:
        owner, provider, account = self._scope_identity(owner_id, provider_id, account_id)
        if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
            raise ValueError("invalid_tool_name")
        self._check_expected(expected_revision)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                revision = self._begin_scope(db, owner, provider, account)
                if revision != expected_revision:
                    raise MCPToolPolicyConflict("stale_policy_revision")
                cursor = db.execute("""DELETE FROM mcp_tool_policies
                    WHERE owner_id=? AND provider_id=? AND account_id=? AND tool_name=?""",
                    (owner, provider, account, tool_name))
                if cursor.rowcount != 1:
                    raise MCPToolPolicyStoreError("policy_not_found")
                next_revision = revision + 1
                db.execute("""UPDATE mcp_tool_policy_revisions SET revision=?
                    WHERE owner_id=? AND provider_id=? AND account_id=? AND revision=?""",
                    (next_revision, owner, provider, account, revision))
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise MCPToolPolicyConflict("stale_policy_revision")
                db.execute("COMMIT")
                return next_revision
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def load_registry(self, *, owner_id, provider_id, account_id) -> MCPToolPolicyRegistry:
        owner, provider, account = self._scope_identity(owner_id, provider_id, account_id)
        with self._connection() as db:
            db.execute("BEGIN")
            try:
                revision_row = db.execute("""SELECT revision FROM mcp_tool_policy_revisions
                    WHERE owner_id=? AND provider_id=? AND account_id=?""",
                    (owner, provider, account)).fetchone()
                revision = revision_row["revision"] if revision_row else 1
                if type(revision) is not int or revision < 1:
                    raise MCPToolPolicyStoreError("policy_revision_corrupt")
                rows = db.execute("""SELECT tool_name, risk, required_scopes_json,
                    version, enabled FROM mcp_tool_policies
                    WHERE owner_id=? AND provider_id=? AND account_id=?
                    ORDER BY tool_name""", (owner, provider, account)).fetchall()
                policies = []
                for row in rows:
                    try:
                        risk = MCPToolRisk(row["risk"])
                        scopes = json.loads(row["required_scopes_json"])
                        if not isinstance(scopes, list):
                            raise ValueError("invalid_scopes")
                        policy = MCPToolPolicy(
                            owner_id=owner, provider_id=provider, account_id=account,
                            tool_name=row["tool_name"], risk=risk,
                            required_scopes=tuple(scopes), version=row["version"],
                            enabled=bool(row["enabled"]),
                        )
                    except Exception:
                        raise MCPToolPolicyStoreError("stored_policy_corrupt") from None
                    self._validate_policy(policy, owner, provider, account)
                    policies.append(policy)
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        try:
            return MCPToolPolicyRegistry(owner_id=owner, policies=tuple(policies), revision=revision)
        except (MCPToolPolicyError, TypeError, ValueError):
            raise MCPToolPolicyStoreError("stored_policy_registry_invalid") from None
