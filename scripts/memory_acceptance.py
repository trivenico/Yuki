"""Opt-in-independent SQLite memory persistence acceptance check.

Uses a temporary database and a trusted in-process authorization policy. It
verifies durable store behavior without model calls or access to user data.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACTOR = "local-alpha-owner"
AGENT = "assistant"
CONTENT = "Yuki local alpha remembers the user's preferred project name."
SOURCE = "local-alpha-acceptance"


def run_acceptance(*, store_factory=None):
    if store_factory is None:
        from core.agent_memory import AgentMemoryStore
        store_factory = AgentMemoryStore

    permissions = {
        "agent_memory:write", "agent_memory:read",
        "agent_memory:delete", "agent_memory:share",
    }
    authorize = lambda actor, action, resource: (
        actor in {ACTOR, "different-owner"} and action in permissions
    )

    with tempfile.TemporaryDirectory(prefix="yuki-memory-alpha-") as directory:
        database = str(Path(directory) / "memory.sqlite3")
        first = store_factory(database, authorize=authorize)
        created = first.remember(
            actor_id=ACTOR, agent_id=AGENT, content=CONTENT,
            source=SOURCE, category="semantic", confidence=1.0,
            verified=True, retention_days=30,
        )
        memory_id = created["memory_id"]

        # Construct a new store instance to model process restart/reopen.
        reopened = store_factory(database, authorize=authorize)
        found = reopened.search(
            actor_id=ACTOR, agent_id=AGENT,
            query="preferred project name", limit=5, include_shared=False,
        )
        matches = [item for item in found if item["memory_id"] == memory_id]
        if len(matches) != 1 or matches[0]["content"] != CONTENT:
            raise RuntimeError("memory_not_persisted_after_store_reopen")

        isolated = reopened.list_memories(
            actor_id="different-owner", agent_id=AGENT, include_shared=False,
        ) if authorize("different-owner", "agent_memory:read", AGENT) else []
        if isolated:
            raise RuntimeError("memory_scope_isolation_failed")

        deleted = reopened.delete(
            actor_id=ACTOR, agent_id=AGENT, memory_id=memory_id,
        )
        if not deleted or reopened.search(
            actor_id=ACTOR, agent_id=AGENT,
            query="preferred project name", limit=5, include_shared=False,
        ):
            raise RuntimeError("memory_delete_verification_failed")

    return {
        "result": "passed",
        "checks": [
            "sqlite_memory_write",
            "persistence_after_store_reopen",
            "actor_scope_isolation",
            "authorized_memory_deletion",
        ],
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "database_or_memory_content_logged": False,
    }


def main() -> int:
    try:
        result = run_acceptance()
    except Exception as exc:
        print(json.dumps({
            "result": "failed",
            "error_type": type(exc).__name__,
            "details": "Inspect local memory storage and authorization configuration; sensitive details are suppressed.",
        }, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
