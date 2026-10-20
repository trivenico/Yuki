"""Phase 39 tests for durable action approval tokens."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.sqlite_action_approvals import SQLiteActionApprovalStore


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SQLiteActionApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "approvals.sqlite3")
        self.clock = FakeClock()
        self.store = SQLiteActionApprovalStore(self.path, ttl_seconds=30, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_token_survives_store_recreation(self):
        item = self.store.create("files.read", {"path": "notes.txt"}, risk="medium")
        restarted = SQLiteActionApprovalStore(self.path, ttl_seconds=30, clock=self.clock)
        consumed = restarted.consume(item.approval_id, skill_id="files.read",
                                     arguments={"path": "notes.txt"}, risk="medium")
        self.assertIsNotNone(consumed)
        self.assertEqual(consumed.arguments, {"path": "notes.txt"})

    def test_token_is_one_time(self):
        item = self.store.create("files.read", {"path": "notes.txt"})
        args = {"path": "notes.txt"}
        self.assertIsNotNone(self.store.consume(item.approval_id, skill_id="files.read", arguments=args, risk="high"))
        self.assertIsNone(self.store.consume(item.approval_id, skill_id="files.read", arguments=args, risk="high"))

    def test_wrong_action_payload_or_risk_burns_token(self):
        item = self.store.create("files.read", {"path": "safe.txt"}, risk="high")
        self.assertIsNone(self.store.consume(item.approval_id, skill_id="files.write",
                                             arguments={"path": "safe.txt"}, risk="high"))
        self.assertIsNone(self.store.consume(item.approval_id, skill_id="files.read",
                                             arguments={"path": "safe.txt"}, risk="high"))

    def test_expired_token_is_rejected(self):
        item = self.store.create("files.read", {"path": "safe.txt"})
        self.clock.advance(31)
        self.assertIsNone(self.store.consume(item.approval_id, skill_id="files.read",
                                             arguments={"path": "safe.txt"}, risk="high"))

    def test_non_json_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create("tool", {"value": float("nan")})

    def test_capacity_is_bounded_and_expiry_frees_capacity(self):
        store = SQLiteActionApprovalStore(self.path, ttl_seconds=2, max_pending=1, clock=self.clock)
        store.create("tool.one", {})
        with self.assertRaises(RuntimeError):
            store.create("tool.two", {})
        self.clock.advance(3)
        store.create("tool.two", {})


if __name__ == "__main__":
    unittest.main()
