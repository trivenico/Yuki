"""Tests for durable, identity-bound, one-time approval snapshots."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.pending_approvals import SQLitePendingApprovalStore


class PendingApprovalStoreTests(unittest.TestCase):
    def make_store(self, root, **kwargs):
        return SQLitePendingApprovalStore(str(Path(root) / "approvals.sqlite3"), **kwargs)

    def test_put_get_and_consume_are_bound_to_actor_and_session(self):
        with tempfile.TemporaryDirectory() as root:
            store = self.make_store(root)
            item = store.put(
                "file.read",
                {"path": "report.txt"},
                {"operation": "read"},
                actor="alice",
                session_id="session-a",
            )
            loaded = store.get(item.pending_id)
            self.assertEqual(loaded.arguments, {"path": "report.txt"})
            self.assertIsNone(store.consume(item.pending_id, actor="bob", session_id="session-a"))
            self.assertIsNone(store.consume(item.pending_id, actor="alice", session_id="session-b"))
            consumed = store.consume(item.pending_id, actor="alice", session_id="session-a")
            self.assertEqual(consumed.action, {"operation": "read"})
            self.assertIsNone(store.get(item.pending_id))
            self.assertIsNone(store.consume(item.pending_id, actor="alice", session_id="session-a"))

    def test_store_rejects_invalid_configuration_and_identity(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "a.sqlite3")
            for kwargs in (
                {"ttl_seconds": 0},
                {"ttl_seconds": 3601},
                {"max_pending": 0},
                {"max_pending": True},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    SQLitePendingApprovalStore(path, **kwargs)
            store = SQLitePendingApprovalStore(path)
            for args in (
                ("", {}, {}, {"actor": "a", "session_id": "s"}),
                ("x" * 121, {}, {}, {"actor": "a", "session_id": "s"}),
                ("skill", {}, {}, {"actor": "", "session_id": "s"}),
                ("skill", {}, {}, {"actor": "a", "session_id": ""}),
                ("skill", [], {}, {"actor": "a", "session_id": "s"}),
                ("skill", {}, {"bad": float("nan")}, {"actor": "a", "session_id": "s"}),
            ):
                skill, arguments, action, identity = args
                with self.subTest(skill=skill), self.assertRaises((ValueError, TypeError)):
                    store.put(skill, arguments, action, **identity)

    def test_capacity_is_bounded_and_deny_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            store = self.make_store(root, max_pending=1)
            item = store.put("a", {}, {}, actor="alice", session_id="s")
            with self.assertRaises(RuntimeError):
                store.put("b", {}, {}, actor="alice", session_id="s")
            self.assertTrue(store.deny(item.pending_id))
            self.assertFalse(store.deny(item.pending_id))

    def test_get_and_consume_reject_empty_and_missing_ids(self):
        with tempfile.TemporaryDirectory() as root:
            store = self.make_store(root)
            self.assertIsNone(store.get(""))
            self.assertIsNone(store.get(None))
            self.assertIsNone(store.consume("", actor="a", session_id="s"))
            self.assertIsNone(store.consume("missing", actor="a", session_id="s"))
            self.assertIsNone(store.consume("missing", actor="", session_id="s"))
            self.assertFalse(store.deny(""))

    def test_persists_across_store_instances_and_hashes_identity(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.make_store(root)
            item = first.put(
                "note", {"x": 1}, {"kind": "note"}, actor="alice", session_id="secret-session"
            )
            second = self.make_store(root)
            self.assertEqual(second.get(item.pending_id).arguments, {"x": 1})
            with sqlite3.connect(second.database_path) as db:
                actor_hash, session_hash = db.execute(
                    "SELECT actor_hash, session_hash FROM pending_approvals WHERE pending_id=?",
                    (item.pending_id,),
                ).fetchone()
            self.assertNotEqual(actor_hash, "alice")
            self.assertNotEqual(session_hash, "secret-session")
            self.assertEqual(len(actor_hash), 64)
            self.assertEqual(len(session_hash), 64)

    def test_legacy_unbound_rows_are_deleted_and_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = self.make_store(root)
            item = store.put("note", {}, {}, actor="alice", session_id="s")
            with store._connect() as db:
                db.execute(
                    "UPDATE pending_approvals SET actor_hash=NULL, session_hash=NULL WHERE pending_id=?",
                    (item.pending_id,),
                )
            self.assertIsNone(store.get(item.pending_id))
            with store._connect() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM pending_approvals WHERE pending_id=?",
                        (item.pending_id,),
                    ).fetchone()[0],
                    0,
                )

    def test_expired_approval_is_removed(self):
        with tempfile.TemporaryDirectory() as root:
            store = self.make_store(root, ttl_seconds=1)
            item = store.put("note", {}, {}, actor="alice", session_id="s")
            with patch("core.pending_approvals.datetime") as clock:
                from datetime import datetime, timedelta, timezone

                future = datetime.now(timezone.utc) + timedelta(days=1)
                clock.now.return_value = future
                clock.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                self.assertIsNone(store.get(item.pending_id))

    def test_snapshot_size_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            store = self.make_store(root)
            with self.assertRaises(ValueError):
                store.put("note", {"payload": "x" * (17 * 1024)}, {}, actor="a", session_id="s")


if __name__ == "__main__":
    unittest.main()
