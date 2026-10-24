"""Persistent approval and safe wiring for already-instantiated trusted skills.

This service never imports manifest entrypoints. It only wires skill instances
provided by a trusted bootstrapper, and only when a reviewed manifest's digest
has a matching explicit approval record.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from skills.metadata_discovery import (
    SkillCandidate, discover_skill_manifests, register_approved_skill,
)


class SkillWiringError(ValueError):
    pass


class SkillApprovalStore:
    """Small SQLite store for explicit manifest-digest approvals."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS skill_approvals (
                    name TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (name, manifest_sha256)
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a transaction-scoped connection and always close its file handle.

        sqlite3.Connection's context manager commits/rolls back but does not close
        the connection. Explicit closure is required for deterministic cleanup,
        especially on Windows where open SQLite files cannot be removed.
        """
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def approve(self, candidate: SkillCandidate, *, approved_by: str) -> None:
        if not isinstance(candidate, SkillCandidate) or candidate.status != "pending_review":
            raise SkillWiringError("only a discovered pending candidate can be approved")
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 128:
            raise SkillWiringError("approved_by must be a non-empty bounded identity")
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO skill_approvals(name, manifest_sha256, approved_by, revoked)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(name, manifest_sha256) DO UPDATE SET
                    approved_by=excluded.approved_by, revoked=0
            """, (candidate.name, candidate.content_sha256, approved_by.strip()))

    def revoke(self, name: str, manifest_sha256: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE skill_approvals SET revoked=1 WHERE name=? AND manifest_sha256=? AND revoked=0",
                (name, manifest_sha256),
            )
            return cursor.rowcount == 1

    def is_approved(self, candidate: SkillCandidate) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM skill_approvals WHERE name=? AND manifest_sha256=? AND revoked=0",
                (candidate.name, candidate.content_sha256),
            ).fetchone()
        return row is not None


class ApprovedSkillWiringService:
    """Discover candidates and wire only matching, explicitly approved instances."""

    def __init__(self, registry: Any, approvals: SkillApprovalStore):
        self.registry = registry
        self.approvals = approvals

    def discover(self, root: str | Path) -> tuple[SkillCandidate, ...]:
        return discover_skill_manifests(root)

    def wire_approved(
        self,
        candidates: tuple[SkillCandidate, ...],
        trusted_instances: dict[str, Any],
    ) -> tuple[str, ...]:
        wired = []
        for candidate in candidates:
            if not self.approvals.is_approved(candidate):
                continue
            skill = trusted_instances.get(candidate.name)
            if skill is None:
                continue
            register_approved_skill(
                self.registry,
                candidate,
                skill,
                approved_names={candidate.name},
                verified_sha256=candidate.content_sha256,
            )
            wired.append(candidate.name)
        return tuple(wired)
