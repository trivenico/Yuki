import asyncio
import tempfile
import unittest
from pathlib import Path

from core.job_queue import SQLiteJobQueue
from core.runtime import RuntimeSettings, RuntimeState, YukiRuntime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.queue = SQLiteJobQueue(Path(tmp.name) / "jobs.sqlite3")

    def test_settings_are_bounded(self):
        with self.assertRaises(ValueError):
            RuntimeSettings(worker_count=0)
        with self.assertRaises(ValueError):
            RuntimeSettings(worker_count=33)

    def test_start_process_and_stop(self):
        async def scenario():
            runtime = YukiRuntime(
                self.queue,
                {"echo": lambda payload: {"echo": payload["value"]}},
                lambda task_type, payload: True,
                settings=RuntimeSettings(worker_count=2, idle_poll_seconds=0.01),
            )
            job = runtime._orchestrators[0].submit("echo", {"value": "hello"})
            await runtime.start()
            for _ in range(100):
                current = self.queue.get(job.id)
                if current.status == "completed":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(self.queue.get(job.id).status, "completed")
            self.assertEqual(self.queue.get(job.id).result, {"echo": "hello"})
            await runtime.stop()
            self.assertEqual(runtime.state, RuntimeState.STOPPED)
            await runtime.stop()  # idempotent
        asyncio.run(scenario())

    def test_context_manager_starts_and_stops(self):
        async def scenario():
            runtime = YukiRuntime(self.queue, {}, lambda task_type, payload: True)
            async with runtime:
                self.assertEqual(runtime.state, RuntimeState.RUNNING)
            self.assertEqual(runtime.state, RuntimeState.STOPPED)
        asyncio.run(scenario())

    def test_runtime_cannot_restart_after_stop(self):
        async def scenario():
            runtime = YukiRuntime(self.queue, {}, lambda task_type, payload: True)
            await runtime.start()
            await runtime.stop()
            with self.assertRaises(RuntimeError):
                await runtime.start()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
