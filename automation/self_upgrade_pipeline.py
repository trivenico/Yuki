"""Proposal-only self-upgrade planning with explicit review boundaries.

This module does not write source files, execute tests, invoke subprocesses, or
promote changes. It creates a bounded digest of a proposed patch so trusted
application code can route it to existing approved workspace-change and test
services in a later, separately authorized workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
import hashlib
import json
import re
from typing import Sequence

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolInputError

_MAX_CHANGES = 8
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024
_BLOCKED_PATH = re.compile(
    r"(^|/)(\.git|\.venv|venv|node_modules|__pycache__|\.ssh|\.aws|\.azure|\.config)(/|$)|"
    r"(^|/)(\.env($|\.)|[^/]*(secret|credential|password|token|private.?key|id_rsa|id_ed25519)[^/]*$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UpgradeProposal:
    proposal_id: str
    content_digest: str
    changed_paths: tuple[str, ...]
    patch_bytes: int
    review_required: bool = True
    tests_required: bool = True
    promotion_allowed: bool = False


class SelfUpgradeProposalBuilder:
    """Validate and fingerprint proposed file changes without applying them."""

    def __init__(self, allowed_paths: Sequence[str]) -> None:
        if not isinstance(allowed_paths, (tuple, list)) or not allowed_paths:
            raise ValueError("An explicit path allow-list is required.")
        if any(not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/") for path in allowed_paths):
            raise ValueError("Allowed paths must be bounded relative paths.")
        self._allowed_paths = frozenset(allowed_paths)

    def build(self, changes: Sequence[FileChange]) -> UpgradeProposal:
        if not isinstance(changes, (tuple, list)) or not 1 <= len(changes) <= _MAX_CHANGES:
            raise ToolInputError("One to eight proposed changes are required.")
        seen: set[str] = set()
        total = 0
        manifest = []
        for change in changes:
            if not isinstance(change, FileChange):
                raise ToolInputError("Every proposal item must be a FileChange.")
            path = change.path
            if not isinstance(path, str) or path not in self._allowed_paths:
                raise ToolInputError("Proposed path is not explicitly allow-listed.")
            if path in seen:
                raise ToolInputError("Duplicate paths are not allowed.")
            if _BLOCKED_PATH.search(path.replace("\\", "/")):
                raise ToolInputError("Sensitive or generated paths are excluded.")
            if not re.fullmatch(r"[0-9a-f]{64}", change.expected_sha256 or ""):
                raise ToolInputError("Expected source SHA-256 digest is invalid.")
            if not isinstance(change.content, str) or "\x00" in change.content:
                raise ToolInputError("Proposed content must be text without NUL bytes.")
            encoded = change.content.encode("utf-8")
            if len(encoded) > _MAX_FILE_BYTES:
                raise ToolInputError("A proposed file exceeds the size limit.")
            total += len(encoded)
            if total > _MAX_TOTAL_BYTES:
                raise ToolInputError("Total proposed patch exceeds the size limit.")
            seen.add(path)
            manifest.append({
                "path": path,
                "expected_sha256": change.expected_sha256,
                "proposed_sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
            })
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        return UpgradeProposal(
            proposal_id="upgrade_" + digest[:24],
            content_digest=digest,
            changed_paths=tuple(sorted(seen)),
            patch_bytes=total,
        )



class SelfUpgradeReviewStore:
    """Durable review decisions for immutable upgrade proposal digests.

    Approval only authorizes a later isolated-testing stage. This store never
    applies files, runs tests, creates commits, or promotes a release.
    """

    def __init__(self, database: str | Path):
        database_path = Path(database).expanduser()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database = str(database_path)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS self_upgrade_reviews (
                    proposal_id TEXT PRIMARY KEY,
                    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
                    changed_paths TEXT NOT NULL,
                    patch_bytes INTEGER NOT NULL CHECK(patch_bytes >= 0),
                    status TEXT NOT NULL CHECK(status IN (
                        'pending_review', 'approved_for_isolated_testing', 'rejected'
                    )),
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    review_note TEXT
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_self_upgrade_reviews_status_created
                ON self_upgrade_reviews(status, created_at)
            """)

    @staticmethod
    def _valid_review_path(path: str) -> bool:
        # The review store records already-built proposals; this is a second,
        # defense-in-depth check against malformed serialized path values.
        normalized = path.replace("\\\\", "/")
        if (not normalized or normalized.startswith("/")
                or any(part in {"", ".", ".."} for part in normalized.split("/"))
                or _BLOCKED_PATH.search(normalized)):
            return False
        return True

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, proposal: UpgradeProposal) -> dict:
        if not isinstance(proposal, UpgradeProposal):
            raise TypeError("proposal must be an UpgradeProposal")
        if (not re.fullmatch(r"upgrade_[0-9a-f]{24}", proposal.proposal_id)
                or not re.fullmatch(r"[0-9a-f]{64}", proposal.content_digest)
                or proposal.promotion_allowed is not False
                or proposal.review_required is not True
                or proposal.tests_required is not True
                or not isinstance(proposal.changed_paths, tuple)
                or not 1 <= len(proposal.changed_paths) <= _MAX_CHANGES
                or any(not isinstance(path, str) for path in proposal.changed_paths)
                or len(set(proposal.changed_paths)) != len(proposal.changed_paths)
                or any(not self._valid_review_path(path) for path in proposal.changed_paths)
                or isinstance(proposal.patch_bytes, bool)
                or not isinstance(proposal.patch_bytes, int)
                or not 0 <= proposal.patch_bytes <= _MAX_TOTAL_BYTES):
            raise ValueError("proposal review invariants are invalid")
        paths = json.dumps(list(proposal.changed_paths), separators=(",", ":"))
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM self_upgrade_reviews WHERE proposal_id = ?",
                (proposal.proposal_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_digest"] != proposal.content_digest:
                    raise ValueError("proposal_id_digest_conflict")
                return dict(existing)
            connection.execute("""
                INSERT INTO self_upgrade_reviews (
                    proposal_id, content_digest, changed_paths, patch_bytes,
                    status, created_at
                ) VALUES (?, ?, ?, ?, 'pending_review', ?)
            """, (
                proposal.proposal_id, proposal.content_digest, paths,
                proposal.patch_bytes, created_at,
            ))
            row = connection.execute(
                "SELECT * FROM self_upgrade_reviews WHERE proposal_id = ?",
                (proposal.proposal_id,),
            ).fetchone()
            return dict(row)

    def review(
        self, proposal_id: str, decision: str, *, reviewer: str, note: str = ""
    ) -> dict:
        if not isinstance(proposal_id, str) or not re.fullmatch(r"upgrade_[0-9a-f]{24}", proposal_id):
            raise ValueError("proposal_id_invalid")
        if decision not in {"approve", "reject"}:
            raise ValueError("decision_must_be_approve_or_reject")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ValueError("reviewer_invalid")
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError("review_note_invalid")
        status = "approved_for_isolated_testing" if decision == "approve" else "rejected"
        reviewed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM self_upgrade_reviews WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ValueError("proposal_not_found")
            if row["status"] != "pending_review":
                if row["status"] == status and row["reviewed_by"] == reviewer:
                    return dict(row)
                raise ValueError("proposal_already_reviewed")
            connection.execute("""
                UPDATE self_upgrade_reviews
                SET status = ?, reviewed_at = ?, reviewed_by = ?, review_note = ?
                WHERE proposal_id = ? AND status = 'pending_review'
            """, (status, reviewed_at, reviewer.strip(), note.strip(), proposal_id))
            updated = connection.execute(
                "SELECT * FROM self_upgrade_reviews WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            return dict(updated)

    def list(self, *, limit: int = 50) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit_must_be_between_1_and_200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM self_upgrade_reviews ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def status(self) -> dict:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM self_upgrade_reviews GROUP BY status"
            ).fetchall()
        counts = {row["status"]: row["total"] for row in rows}
        return {
            "total": sum(counts.values()),
            "pending_review": counts.get("pending_review", 0),
            "approved_for_isolated_testing": counts.get("approved_for_isolated_testing", 0),
            "rejected": counts.get("rejected", 0),
            "automatic_application_enabled": False,
            "automatic_promotion_enabled": False,
        }
