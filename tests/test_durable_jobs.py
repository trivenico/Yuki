import tempfile
import unittest
from pathlib import Path

from core.durable_jobs import IdempotencyConflict, JobStore


class DurableJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "jobs.sqlite3"
        self.store = JobStore(self.db)

    def test_job_survives_store_recreation(self):
        job = self.store.create_job("summarize", {"text": "hello"})
        reopened = JobStore(self.db).get_job(job.job_id)
        self.assertEqual(reopened.payload, {"text": "hello"})
        self.assertEqual(reopened.status, "queued")

    def test_idempotency_key_returns_same_job_for_same_operation(self):
        first = self.store.create_job("summarize", {"text": "hello"}, idempotency_key="req-1")
        second = self.store.create_job("summarize", {"text": "hello"}, idempotency_key="req-1")
        self.assertEqual(first.job_id, second.job_id)
        with self.assertRaises(IdempotencyConflict):
            self.store.create_job("summarize", {"text": "different"}, idempotency_key="req-1")

    def test_claim_and_finish(self):
        created = self.store.create_job("summarize", {"text": "hello"})
        claimed = self.store.claim_next()
        self.assertEqual(claimed.job_id, created.job_id)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(claimed.attempts, 1)
        finished = self.store.finish(claimed.job_id, result={"summary": "hello"})
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(finished.result, {"summary": "hello"})
        self.assertIsNone(self.store.claim_next())

    def test_retry_budget_is_bounded(self):
        job = self.store.create_job("summarize", {}, max_attempts=2)
        claimed = self.store.claim_next()
        retry = self.store.fail(claimed.job_id, error_code="temporary", retryable=True)
        self.assertEqual(retry.status, "retry_wait")
        claimed = self.store.claim_next()
        self.assertEqual(claimed.attempts, 2)
        failed = self.store.fail(claimed.job_id, error_code="temporary", retryable=True)
        self.assertEqual(failed.status, "failed")
        self.assertIsNone(self.store.claim_next())

    def test_cancel_queued_job(self):
        job = self.store.create_job("summarize", {})
        self.assertTrue(self.store.request_cancel(job.job_id))
        self.assertEqual(self.store.get_job(job.job_id).status, "cancelled")
        self.assertIsNone(self.store.claim_next())

    def test_running_cancel_requires_handler_cooperation(self):
        job = self.store.create_job("summarize", {})
        claimed = self.store.claim_next()
        self.assertTrue(self.store.request_cancel(job.job_id))
        self.assertTrue(self.store.cancellation_requested(job.job_id))
        with self.assertRaises(ValueError):
            self.store.finish(job.job_id, result="should not commit")

    def test_payload_and_attempt_limits_are_validated(self):
        with self.assertRaises(ValueError):
            self.store.create_job("bad", {"nan": float("nan")})
        with self.assertRaises(ValueError):
            self.store.create_job("bad", {}, max_attempts=99)

    def test_stale_running_jobs_recover_with_bounded_attempts(self):
        job = self.store.create_job("summarize", {}, max_attempts=2)
        self.store.claim_next()
        # Make this job appear interrupted long ago.
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE job_id=?", (job.job_id,))
        report = self.store.recover_stale_running(older_than_seconds=30)
        self.assertEqual(report["requeued"], 1)
        self.assertEqual(self.store.get_job(job.job_id).status, "retry_wait")
        self.assertEqual(self.store.claim_next().attempts, 2)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE job_id=?", (job.job_id,))
        report = self.store.recover_stale_running(older_than_seconds=30)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(self.store.get_job(job.job_id).status, "failed")


if __name__ == "__main__":
    unittest.main()
