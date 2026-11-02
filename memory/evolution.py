"""Human-approved, version-checked proposals for evolving Yuki memories.

This service never auto-applies findings. A reviewer must explicitly approve
each proposal; the source memory version is checked immediately before update.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4

from memory.collective_vault import CollectiveMemoryVault, MemoryVaultError


class EvolutionError(ValueError):
    """Invalid proposal or state transition."""


@dataclass(frozen=True)
class EvolutionProposal:
    proposal_id: str
    memory_id: str
    scope: str
    scope_id: str
    expected_version: int
    proposed_content: str
    rationale: str
    evidence_ref: str
    created_by: str
    status: str
    reviewed_by: str | None
    created_at: str
    reviewed_at: str | None


class KnowledgeEvolutionEngine:
    """Persist proposals separately and apply only explicitly approved updates."""

    def __init__(
        self, vault: CollectiveMemoryVault, database_path: str | None = None,
        *, authorize: Callable[[str, str, str], bool],
        require_independent_reviewer: bool = True,
    ):
        if not isinstance(vault, CollectiveMemoryVault):
            raise TypeError("vault must be a CollectiveMemoryVault.")
        if not callable(authorize):
            raise TypeError("a trusted proposal authorization callback is required.")
        self.vault = vault
        self.database_path = str(Path(database_path or vault.database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        self.require_independent_reviewer = bool(require_independent_reviewer)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS evolution_proposals (
                proposal_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL,
                scope TEXT NOT NULL, scope_id TEXT NOT NULL,
                expected_version INTEGER NOT NULL, proposed_content TEXT NOT NULL,
                rationale TEXT NOT NULL, evidence_ref TEXT NOT NULL,
                created_by TEXT NOT NULL, status TEXT NOT NULL,
                reviewed_by TEXT, created_at TEXT NOT NULL, reviewed_at TEXT
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_evolution_status
                ON evolution_proposals(status, created_at)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    def _allowed(self, actor, action, resource):
        try:
            return self.authorize(actor, action, resource) is True
        except Exception:
            return False

    @staticmethod
    def _text(value, name, limit):
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise EvolutionError(f"{name} must be non-empty bounded text.")
        return value.strip()

    @staticmethod
    def _proposal(row):
        return EvolutionProposal(**dict(row))

    def propose(self, *, actor_id: str, memory_id: str, proposed_content: str,
                rationale: str, evidence_ref: str) -> EvolutionProposal:
        actor = self._text(actor_id, "actor_id", 100)
        content = self._text(proposed_content, "proposed_content", 12000)
        why = self._text(rationale, "rationale", 2000)
        evidence = self._text(evidence_ref, "evidence_ref", 500)
        record = self.vault.get(actor_id=actor, memory_id=memory_id)
        if record is None:
            raise EvolutionError("source memory does not exist or is not readable.")
        resource = f"{record.scope}:{record.scope_id}"
        if not self._allowed(actor, "evolution:propose", resource):
            raise PermissionError("knowledge evolution proposal is not authorized.")
        if content == record.content:
            raise EvolutionError("proposal must change the source content.")
        now = datetime.now(timezone.utc).isoformat()
        proposal_id = uuid4().hex
        with self._connect() as db:
            db.execute("""INSERT INTO evolution_proposals VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)""",
                (proposal_id, record.memory_id, record.scope, record.scope_id,
                 record.version, content, why, evidence, actor, now))
            row = db.execute("SELECT * FROM evolution_proposals WHERE proposal_id=?",
                             (proposal_id,)).fetchone()
        return self._proposal(row)

    def get(self, *, actor_id: str, proposal_id: str) -> EvolutionProposal | None:
        actor = self._text(actor_id, "actor_id", 100)
        pid = self._text(proposal_id, "proposal_id", 100)
        with self._connect() as db:
            row = db.execute("SELECT * FROM evolution_proposals WHERE proposal_id=?", (pid,)).fetchone()
        if row is None:
            return None
        resource = f"{row['scope']}:{row['scope_id']}"
        if not self._allowed(actor, "evolution:read", resource):
            raise PermissionError("proposal read is not authorized.")
        return self._proposal(row)

    def review(self, *, reviewer_id: str, proposal_id: str, decision: str) -> EvolutionProposal:
        reviewer = self._text(reviewer_id, "reviewer_id", 100)
        pid = self._text(proposal_id, "proposal_id", 100)
        if decision not in {"approve", "reject"}:
            raise EvolutionError("decision must be approve or reject.")
        # Claim the pending proposal in a short transaction. Never hold a
        # SQLite write lock while calling into the separately connected vault.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM evolution_proposals WHERE proposal_id=?", (pid,)).fetchone()
            if row is None:
                raise EvolutionError("proposal not found.")
            proposal = self._proposal(row)
            resource = f"{proposal.scope}:{proposal.scope_id}"
            if not self._allowed(reviewer, "evolution:review", resource):
                raise PermissionError("proposal review is not authorized.")
            if proposal.status != "pending":
                raise EvolutionError("proposal has already been reviewed or is being reviewed.")
            if self.require_independent_reviewer and reviewer == proposal.created_by:
                raise PermissionError("a different reviewer must approve this proposal.")
            now = datetime.now(timezone.utc).isoformat()
            claimed = "reviewing" if decision == "approve" else "rejected"
            db.execute("""UPDATE evolution_proposals SET status=?, reviewed_by=?, reviewed_at=?
                WHERE proposal_id=? AND status='pending'""", (claimed, reviewer, now, pid))
            if db.total_changes < 1:
                raise EvolutionError("proposal was concurrently reviewed.")

        if decision == "approve":
            try:
                current = self.vault.get(actor_id=reviewer, memory_id=proposal.memory_id)
                if current is None or current.version != proposal.expected_version:
                    raise EvolutionError("source memory changed; create a fresh proposal.")
                self.vault.remember(
                    actor_id=reviewer, scope=proposal.scope, scope_id=proposal.scope_id,
                    category=current.category, content=proposal.proposed_content,
                    source_type="approved_knowledge_evolution",
                    source_ref=proposal.evidence_ref, memory_id=proposal.memory_id,
                )
            except Exception:
                # Release the claim for a retry if the source update failed.
                with self._connect() as db:
                    db.execute("""UPDATE evolution_proposals SET status='pending',
                        reviewed_by=NULL, reviewed_at=NULL
                        WHERE proposal_id=? AND status='reviewing'""", (pid,))
                raise
            with self._connect() as db:
                db.execute("""UPDATE evolution_proposals SET status='approved'
                    WHERE proposal_id=? AND status='reviewing'""", (pid,))
        result = self.get(actor_id=reviewer, proposal_id=pid)
        if result is None:
            raise EvolutionError("reviewed proposal could not be reloaded.")
        return result
