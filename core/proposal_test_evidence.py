"""Append-only evidence for supervised self-improvement test runs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any


class ProposalTestEvidenceError(ValueError):
    """Test evidence is malformed or its integrity chain is invalid."""


class ProposalTestEvidenceStore:
    """SQLite append-only hash chain; records metadata and output digests, not raw logs."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS proposal_test_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    recorded_at TEXT NOT NULL,
                    proposal_sha256 TEXT NOT NULL,
                    workspace_name TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
            """)
            db.execute("""CREATE TRIGGER IF NOT EXISTS proposal_test_evidence_no_update
                BEFORE UPDATE ON proposal_test_evidence
                BEGIN SELECT RAISE(ABORT, 'test evidence is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS proposal_test_evidence_no_delete
                BEFORE DELETE ON proposal_test_evidence
                BEGIN SELECT RAISE(ABORT, 'test evidence is append-only'); END""")

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    def append(self, *, proposal_sha256: str, workspace_name: str,
               results: list[dict[str, Any]]) -> str:
        if not isinstance(proposal_sha256, str) or len(proposal_sha256) != 64 or any(c not in "0123456789abcdef" for c in proposal_sha256.lower()):
            raise ProposalTestEvidenceError("proposal digest must be a SHA-256 hex digest")
        if not isinstance(workspace_name, str) or not workspace_name.startswith("yuki-improvement-") or "/" in workspace_name or "\\" in workspace_name:
            raise ProposalTestEvidenceError("workspace identity is invalid")
        if not isinstance(results, list) or not results or len(results) > 32:
            raise ProposalTestEvidenceError("results must contain 1 to 32 checks")
        safe_results = []
        for item in results:
            if not isinstance(item, dict) or set(item) != {"command", "returncode", "timed_out", "output_truncated", "cleanup_attempted", "stdout_sha256", "stderr_sha256"}:
                raise ProposalTestEvidenceError("test result schema is invalid")
            if not isinstance(item["command"], list) or any(not isinstance(arg, str) for arg in item["command"]):
                raise ProposalTestEvidenceError("test command must be an argument array")
            if type(item["returncode"]) is not int or type(item["timed_out"]) is not bool or type(item["output_truncated"]) is not bool or type(item["cleanup_attempted"]) is not bool:
                raise ProposalTestEvidenceError("test result status fields have invalid types")
            for field in ("stdout_sha256", "stderr_sha256"):
                digest = item[field]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                    raise ProposalTestEvidenceError("output digest must be SHA-256 hex")
            safe_results.append(item)
        encoded = json.dumps(safe_results, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        run_id = uuid.uuid4().hex
        recorded_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT event_hash FROM proposal_test_evidence ORDER BY id DESC LIMIT 1").fetchone()
                previous_hash = row[0] if row else "0" * 64
                event_payload = json.dumps({
                    "run_id": run_id, "recorded_at": recorded_at,
                    "proposal_sha256": proposal_sha256.lower(),
                    "workspace_name": workspace_name, "payload_sha256": payload_sha,
                    "previous_hash": previous_hash,
                }, sort_keys=True, separators=(",", ":"))
                event_hash = hashlib.sha256(event_payload.encode("utf-8")).hexdigest()
                db.execute("""INSERT INTO proposal_test_evidence
                    (run_id, recorded_at, proposal_sha256, workspace_name, result_json,
                     payload_sha256, previous_hash, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, recorded_at, proposal_sha256.lower(), workspace_name,
                     encoded, payload_sha, previous_hash, event_hash))
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            finally:
                db.close()
        return run_id

    def verify_chain(self) -> bool:
        """Verify every payload digest, event hash, and previous-link in order."""
        with self._connect() as db:
            rows = db.execute("""SELECT run_id, recorded_at, proposal_sha256,
                workspace_name, result_json, payload_sha256, previous_hash, event_hash
                FROM proposal_test_evidence ORDER BY id ASC""").fetchall()
        previous = "0" * 64
        for row in rows:
            run_id, recorded_at, proposal_sha, workspace, result_json, payload_sha, previous_hash, event_hash = row
            if previous_hash != previous:
                return False
            if hashlib.sha256(result_json.encode("utf-8")).hexdigest() != payload_sha:
                return False
            event_payload = json.dumps({
                "run_id": run_id, "recorded_at": recorded_at,
                "proposal_sha256": proposal_sha, "workspace_name": workspace,
                "payload_sha256": payload_sha, "previous_hash": previous_hash,
            }, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(event_payload.encode("utf-8")).hexdigest() != event_hash:
                return False
            previous = event_hash
        return True

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ProposalTestEvidenceError("limit must be between 1 and 100")
        with self._connect() as db:
            rows = db.execute("""SELECT run_id, recorded_at, proposal_sha256,
                workspace_name, result_json, payload_sha256, previous_hash, event_hash
                FROM proposal_test_evidence ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
        output = []
        for row in rows:
            output.append({
                "run_id": row[0], "recorded_at": row[1], "proposal_sha256": row[2],
                "workspace_name": row[3], "results": json.loads(row[4]),
                "payload_sha256": row[5], "previous_hash": row[6], "event_hash": row[7],
            })
        return output
