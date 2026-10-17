import tempfile
import unittest
from pathlib import Path

from core.durable_jobs import JobStore
from core.task_worker import RetryableTaskError, TaskWorker


class TaskWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobStore(Path(self.temp.name) / "jobs.sqlite3")

    def test_executes_registered_handler_and_persists_result(self):
        job = self.store.create_job("sum", {"a": 2, "b": 3})
        worker = TaskWorker(self.store, handlers={"sum": lambda payload, ctx: {"value": payload["a"] + payload["b"]}})
        result = worker.run_once()
        self.assertEqual(result.job_id, job.job_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.result, {"value": 5})
        self.assertEqual(self.store.get_job(job.job_id).status, "succeeded")

    def test_unknown_handler_fails_closed(self):
        job = self.store.create_job("unknown", {})
        result = TaskWorker(self.store).run_once()
        self.assertEqual(result.job_id, job.job_id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "handler_not_registered")

    def test_retryable_error_retries_only_with_budget(self):
        job = self.store.create_job("flaky", {}, max_attempts=2)
        worker = TaskWorker(self.store, handlers={"flaky": lambda payload, ctx: (_ for _ in ()).throw(RetryableTaskError())})
        first = worker.run_once()
        self.assertEqual(first.status, "retry_wait")
        second = worker.run_once()
        self.assertEqual(second.status, "failed")
        self.assertEqual(second.attempts, 2)

    def test_handler_exception_is_redacted(self):
        job = self.store.create_job("boom", {})
        worker = TaskWorker(self.store, handlers={"boom": lambda payload, ctx: (_ for _ in ()).throw(RuntimeError("api_key=secret"))})
        result = worker.run_once()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "task_handler_failed")
        self.assertNotIn("secret", str(result))

    def test_cancelled_job_is_not_claimed(self):
        job = self.store.create_job("sum", {"a": 1})
        self.assertTrue(self.store.request_cancel(job.job_id))
        worker = TaskWorker(self.store, handlers={"sum": lambda payload, ctx: {"value": 1}})
        self.assertIsNone(worker.run_once())
        self.assertEqual(self.store.get_job(job.job_id).status, "cancelled")

    def test_handler_can_observe_cancellation(self):
        observed = []
        job = self.store.create_job("check", {})
        worker = TaskWorker(self.store, handlers={"check": lambda payload, ctx: {"cancelled": observed.append(ctx.cancelled()) or ctx.cancelled()}})
        result = worker.run_once()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.result, {"cancelled": False})


if __name__ == "__main__":
    unittest.main()
