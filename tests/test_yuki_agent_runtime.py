import tempfile
import unittest
from pathlib import Path

from yuki_agent_runtime import TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "tasks.sqlite3")

    def tearDown(self):
        # Close every connection before deleting the temporary directory; Windows
        # does not permit removal of an open SQLite database file.
        with self.store._session():
            pass
        self.temp.cleanup()

    def test_approval_is_required_by_default(self):
        task = self.store.submit("coding", {"goal": "review tests"})
        self.assertEqual(task["status"], "queued")
        self.assertTrue(task["approval_required"])
        self.assertIsNone(self.store.claim_next("worker-a"))
        approved = self.store.approve(task["id"], approved_by="owner")
        self.assertTrue(approved["approved"])
        claimed = self.store.claim_next("worker-a")
        self.assertEqual(claimed["id"], task["id"])
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["attempts"], 1)

    def test_no_approval_needed_when_explicitly_disabled(self):
        task = self.store.submit("read-only", {}, approval_required=False)
        self.assertIsNotNone(self.store.claim_next("worker"))
        self.assertEqual(self.store.get(task["id"])["status"], "running")

    def test_idempotency_key_prevents_duplicate_tasks(self):
        first = self.store.submit("research", {"q": "models"}, idempotency_key="research-1")
        second = self.store.submit("research", {"q": "different"}, idempotency_key="research-1")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_completion_persists_result_and_event_ledger(self):
        task = self.store.submit("test", {"n": 1}, approval_required=False)
        self.store.claim_next("worker")
        finished = self.store.complete(task["id"], "worker", {"passed": True})
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["result"], {"passed": True})
        self.assertEqual([e["event"] for e in self.store.events(task["id"])],
                         ["submitted", "claimed", "succeeded"])

    def test_wrong_worker_cannot_complete_claimed_task(self):
        task = self.store.submit("test", {}, approval_required=False)
        self.store.claim_next("worker-a")
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.store.complete(task["id"], "worker-b", {"ok": True})
        self.assertEqual(self.store.get(task["id"])["status"], "running")

    def test_retry_respects_attempt_budget(self):
        task = self.store.submit("test", {}, approval_required=False, max_attempts=2)
        self.store.claim_next("worker")
        retried = self.store.fail(task["id"], "worker", "transient", retryable=True)
        self.assertEqual(retried["status"], "queued")
        self.store.claim_next("worker")
        failed = self.store.fail(task["id"], "worker", "still failing", retryable=True)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], 2)

    def test_cancelled_task_cannot_be_claimed_or_completed(self):
        task = self.store.submit("test", {}, approval_required=False)
        self.store.cancel(task["id"], requested_by="owner")
        self.assertIsNone(self.store.claim_next("worker"))
        self.assertEqual(self.store.get(task["id"])["status"], "cancelled")

    def test_lease_expiry_requeues_until_attempt_limit(self):
        task = self.store.submit("test", {}, approval_required=False, max_attempts=2)
        first = self.store.claim_next("worker-a", lease_seconds=1)
        # Set an expired lease directly to avoid a timing-dependent sleep.
        with self.store._session() as db:
            db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))
        second = self.store.claim_next("worker-b")
        self.assertEqual(second["id"], task["id"])
        self.assertEqual(second["attempts"], 2)
        with self.store._session() as db:
            db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))
        self.assertIsNone(self.store.claim_next("worker-c"))
        self.assertEqual(self.store.get(task["id"])["status"], "failed")

    def test_invalid_payload_and_budgets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            self.store.submit("test", {"not_json": object()})
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            self.store.submit("test", {}, max_attempts=0)
        with self.assertRaisesRegex(ValueError, "budget_tokens"):
            self.store.submit("test", {}, budget_tokens=-1)

    def test_rejects_non_string_nested_payload_keys(self):
        with self.assertRaisesRegex(ValueError, "string object keys"):
            self.store.submit("test", {"nested": {1: "ambiguous"}})
        self.assertEqual(self.store.list_tasks(), [])

    def test_rejects_payloads_over_size_limit_before_persisting(self):
        from yuki_agent_runtime import MAX_TASK_PAYLOAD_BYTES
        oversized = {"data": "x" * MAX_TASK_PAYLOAD_BYTES}
        with self.assertRaisesRegex(ValueError, "task limit"):
            self.store.submit("test", oversized)
        self.assertEqual(self.store.list_tasks(), [])

    def test_rejects_non_persistent_memory_database(self):
        with self.assertRaisesRegex(ValueError, "persistent database path"):
            TaskStore(":memory:")

    def test_reopens_database_and_lists_by_status(self):
        task = self.store.submit("test", {"x": 1}, approval_required=False)
        reopened = TaskStore(Path(self.temp.name) / "tasks.sqlite3")
        self.assertEqual(reopened.get(task["id"])["payload"], {"x": 1})
        self.assertEqual(len(reopened.list_tasks(status="queued")), 1)


if __name__ == "__main__":
    unittest.main()
