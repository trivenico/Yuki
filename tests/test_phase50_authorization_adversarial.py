"""Phase 50 adversarial tests for replay and concurrent token consumption."""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.sqlite_action_approvals import SQLiteActionApprovalStore


class Phase50AuthorizationAdversarialTests(unittest.TestCase):
    def test_concurrent_consumers_can_claim_a_token_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "approvals.sqlite3")
            store = SQLiteActionApprovalStore(path)
            item = store.create("notes.write", {"text": "approved"}, risk="low")

            def consume():
                return store.consume(
                    item.approval_id,
                    skill_id="notes.write",
                    arguments={"text": "approved"},
                    risk="low",
                )

            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(lambda _: consume(), range(12)))

            self.assertEqual(sum(result is not None for result in results), 1)

    def test_wrong_payload_burns_token_and_original_payload_cannot_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteActionApprovalStore(str(Path(directory) / "approvals.sqlite3"))
            item = store.create("notes.write", {"text": "approved"}, risk="low")

            changed = store.consume(
                item.approval_id,
                skill_id="notes.write",
                arguments={"text": "changed"},
                risk="low",
            )
            replay = store.consume(
                item.approval_id,
                skill_id="notes.write",
                arguments={"text": "approved"},
                risk="low",
            )

            self.assertIsNone(changed)
            self.assertIsNone(replay)

    def test_restart_preserves_single_use_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "approvals.sqlite3")
            first = SQLiteActionApprovalStore(path)
            item = first.create("notes.write", {"text": "approved"}, risk="low")
            restarted = SQLiteActionApprovalStore(path)

            consumed = restarted.consume(
                item.approval_id,
                skill_id="notes.write",
                arguments={"text": "approved"},
                risk="low",
            )
            replay = first.consume(
                item.approval_id,
                skill_id="notes.write",
                arguments={"text": "approved"},
                risk="low",
            )

            self.assertIsNotNone(consumed)
            self.assertIsNone(replay)


if __name__ == "__main__":
    unittest.main()
