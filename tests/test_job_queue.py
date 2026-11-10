import tempfile
import unittest
from pathlib import Path
from core.job_queue import SQLiteJobQueue

class SQLiteJobQueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "jobs.sqlite3"
        self.queue = SQLiteJobQueue(self.path)

    def test_enqueue_claim_complete_persists(self):
        queued = self.queue.enqueue("research", {"goal": "compare options"})
        claimed = self.queue.claim_next("worker-a")
        self.assertEqual(claimed.id, queued.id)
        self.assertEqual((claimed.status, claimed.attempts), ("running", 1))
        done = self.queue.complete(claimed.id, "worker-a", {"summary": "ready"})
        self.assertEqual(done.status, "completed")
        self.assertEqual(SQLiteJobQueue(self.path).get(queued.id).result, {"summary": "ready"})

    def test_worker_ownership_is_enforced(self):
        job = self.queue.enqueue("write", {"text": "draft"})
        self.queue.claim_next("worker-a")
        with self.assertRaises(ValueError):
            self.queue.complete(job.id, "worker-b", {})
        self.assertEqual(self.queue.get(job.id).status, "running")

    def test_failure_retries_within_budget(self):
        job = self.queue.enqueue("research", {}, max_attempts=2)
        first = self.queue.claim_next("worker-a")
        self.assertEqual(self.queue.fail(first.id, "worker-a", "temporary").status, "queued")
        second = self.queue.claim_next("worker-b")
        self.assertEqual(self.queue.fail(second.id, "worker-b", "still broken").status, "failed")

    def test_expired_lease_retries_then_fails(self):
        job = self.queue.enqueue("research", {}, max_attempts=2)
        self.queue.claim_next("worker-a", lease_seconds=1, now=100)
        second = self.queue.claim_next("worker-b", lease_seconds=1, now=102)
        self.assertEqual((second.id, second.attempts), (job.id, 2))
        self.assertIsNone(self.queue.claim_next("worker-c", now=104))
        self.assertEqual(self.queue.get(job.id).status, "failed")

    def test_cancel_prevents_claim(self):
        job = self.queue.enqueue("research", {})
        self.assertEqual(self.queue.cancel(job.id).status, "cancelled")
        self.assertIsNone(self.queue.claim_next("worker-a"))
        with self.assertRaises(ValueError):
            self.queue.cancel(job.id)

    def test_input_validation(self):
        with self.assertRaises(TypeError): self.queue.enqueue("research", [])
        with self.assertRaises(ValueError): self.queue.enqueue("research", {"v": float("nan")})
        with self.assertRaises(ValueError): self.queue.enqueue("research", {}, max_attempts=0)
        with self.assertRaises(ValueError): self.queue.claim_next("worker", lease_seconds=0)

    def test_missing_job(self):
        self.assertIsNone(self.queue.get("missing"))
        with self.assertRaises(KeyError): self.queue.cancel("missing")

if __name__ == "__main__":
    unittest.main()
