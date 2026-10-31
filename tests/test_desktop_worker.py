import queue
import threading
import time
import unittest

from core.desktop_worker import DesktopRuntimeWorker


class FakeStore:
    def list_conversations(self):
        return [{"conversation_id": "c1", "title": "Test"}]


class FakeRuntime:
    def __init__(self):
        self.owner = threading.get_ident()
        self.active_conversation_id = "c1"
        self.conversations = FakeStore()
        self.closed = False

    def _check_thread(self):
        if threading.get_ident() != self.owner:
            raise AssertionError("Runtime accessed from a non-owner thread")

    def send_message(self, message):
        self._check_thread()
        return "reply:" + message

    def history(self):
        self._check_thread()
        return [{"role": "user", "content": "hello"}]

    def new_conversation(self, title):
        self._check_thread()
        self.active_conversation_id = "c2"
        return "c2"

    def switch_conversation(self, conversation_id):
        self._check_thread()
        self.active_conversation_id = conversation_id

    def close(self):
        self._check_thread()
        self.closed = True


class DesktopWorkerTests(unittest.TestCase):
    def _next(self, worker, action, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = worker.results.get(timeout=0.05)
            except queue.Empty:
                continue
            if event.get("action") == action:
                return event
        self.fail("Timed out waiting for " + action)

    def test_runtime_is_created_used_and_closed_on_worker_thread(self):
        runtimes = []
        def factory():
            runtime = FakeRuntime()
            runtimes.append(runtime)
            return runtime

        worker = DesktopRuntimeWorker(factory)
        worker.start()
        self.assertTrue(self._next(worker, "ready")["ok"])
        request_id = worker.submit("send", message="hello")
        response = self._next(worker, "send")
        self.assertEqual(response["request_id"], request_id)
        self.assertEqual(response["response"], "reply:hello")
        worker.submit("history")
        self.assertEqual(len(self._next(worker, "history")["messages"]), 1)
        worker.submit("close")
        self.assertTrue(self._next(worker, "close")["ok"])
        worker.join(2)
        self.assertFalse(worker._thread.is_alive())
        self.assertTrue(runtimes[0].closed)

    def test_initialization_failure_is_reported_without_leaking_exception(self):
        worker = DesktopRuntimeWorker(lambda: (_ for _ in ()).throw(RuntimeError("private")))
        worker.start()
        event = self._next(worker, "ready")
        self.assertFalse(event["ok"])
        self.assertEqual(event["error"], "RuntimeError")
        worker.join(2)
        with self.assertRaises(RuntimeError):
            worker.submit("history")

    def test_unsupported_actions_are_rejected(self):
        worker = DesktopRuntimeWorker()
        with self.assertRaises(ValueError):
            worker.submit("execute_shell")
        self.assertFalse(worker._started)


if __name__ == "__main__":
    unittest.main()
