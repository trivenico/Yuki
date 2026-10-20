"""Regression tests for Phase 38 approval integrity."""
from __future__ import annotations

import unittest

from core.action_approvals import ActionApprovalStore


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ActionApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = ActionApprovalStore(ttl_seconds=30, clock=self.clock)

    def test_approval_is_bound_to_action_risk_and_canonical_payload(self):
        item = self.store.create("files.read", {"path": "notes.txt"}, risk="medium")
        self.assertIsNone(self.store.consume(
            item.approval_id, skill_id="files.write",
            arguments={"path": "notes.txt"}, risk="medium",
        ))
        # Mismatch burns the one-time token: it cannot later be replayed.
        self.assertIsNone(self.store.consume(
            item.approval_id, skill_id="files.read",
            arguments={"path": "notes.txt"}, risk="medium",
        ))

    def test_payload_mutation_is_rejected(self):
        item = self.store.create("files.read", {"path": "safe.txt"}, risk="high")
        self.assertIsNone(self.store.consume(
            item.approval_id, skill_id="files.read",
            arguments={"path": "other.txt"}, risk="high",
        ))

    def test_valid_approval_is_consumed_once(self):
        item = self.store.create("files.read", {"path": "safe.txt"}, risk="high")
        consumed = self.store.consume(
            item.approval_id, skill_id="files.read",
            arguments={"path": "safe.txt"}, risk="high",
        )
        self.assertIsNotNone(consumed)
        self.assertEqual(consumed.arguments, {"path": "safe.txt"})
        self.assertIsNone(self.store.consume(
            item.approval_id, skill_id="files.read",
            arguments={"path": "safe.txt"}, risk="high",
        ))

    def test_expired_approval_is_rejected(self):
        item = self.store.create("files.read", {"path": "safe.txt"}, risk="high")
        self.clock.advance(31)
        self.assertIsNone(self.store.consume(
            item.approval_id, skill_id="files.read",
            arguments={"path": "safe.txt"}, risk="high",
        ))

    def test_mutating_returned_snapshot_does_not_mutate_pending_record(self):
        item = self.store.create("files.read", {"path": "safe.txt"}, risk="high")
        item.arguments["path"] = "attacker.txt"
        consumed = self.store.consume(
            item.approval_id, skill_id="files.read",
            arguments={"path": "safe.txt"}, risk="high",
        )
        self.assertIsNotNone(consumed)
        self.assertEqual(consumed.arguments, {"path": "safe.txt"})

    def test_non_json_and_non_finite_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create("tool", {"bad": object()})
        with self.assertRaises(ValueError):
            self.store.create("tool", {"bad": float("nan")})

    def test_risk_mismatch_is_rejected(self):
        item = self.store.create("files.read", {"path": "safe.txt"}, risk="high")
        self.assertIsNone(self.store.consume(
            item.approval_id, skill_id="files.read",
            arguments={"path": "safe.txt"}, risk="critical",
        ))


if __name__ == "__main__":
    unittest.main()
