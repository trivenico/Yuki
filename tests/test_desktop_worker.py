import queue
import threading
import time
import unittest

from core.desktop_worker import DesktopRuntimeWorker


class FakeStore:
    def list_conversations(self):
        return [{"conversation_id": "c1", "title": "Test"}]


class FakeProviders:
    def __init__(self):
        self.selected = "openai"

    def current(self):
        return type("Provider", (), {"name": self.selected})()

    def list_providers(self):
        return [
            {"name": "openai", "implemented": True, "selected": self.selected == "openai"},
            {"name": "anthropic", "implemented": False, "selected": False},
        ]

    def select(self, name):
        if name != "openai":
            return f"{name} is registered but not implemented."
        self.selected = name
        return "Selected provider: openai"

    def create_selected_adapter(self):
        return object()

    def catalog(self):
        return [{
            "name": "openai", "configured_model": "gpt-4.1-mini",
            "privacy": "cloud_provider", "credential_configured": True,
            "ready": True, "status": "api_key_and_model_configured",
            "selected": True, "implemented": True,
        }]


class FakeAgent:
    def set_provider(self, adapter):
        self.provider = adapter


class FakeRuntime:
    def __init__(self):
        self.owner = threading.get_ident()
        self.active_conversation_id = "c1"
        self.conversations = FakeStore()
        self.providers = FakeProviders()
        self.agent = FakeAgent()
        self.closed = False

    def _check_thread(self):
        if threading.get_ident() != self.owner:
            raise AssertionError("Runtime accessed from a non-owner thread")

    def send_message(self, message):
        self._check_thread()
        return "reply:" + message

    def stream_message(self, message):
        self._check_thread()
        self.conversations.streamed = []
        for chunk in ("reply:", message):
            self.conversations.streamed.append(chunk)
            yield chunk

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


class FakeSyncRuntime:
    def __init__(self):
        self.paired = False
        self.closed = False

    def status(self):
        return "Hub: https://sync.example.test\\nDevice: " + ("paired" if self.paired else "not paired")

    def pair(self, code, device_label):
        if code != "one-time-code":
            raise ValueError("invalid pairing code")
        self.paired = True
        return "This device is paired."

    def close(self):
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

    def test_stream_action_emits_incremental_chunks_and_final_response(self):
        worker = DesktopRuntimeWorker(FakeRuntime)
        worker.start()
        self._next(worker, "ready")
        request_id = worker.submit("stream", message="hello")
        first = self._next(worker, "chunk")
        second = self._next(worker, "chunk")
        final = self._next(worker, "stream")
        self.assertEqual(first["chunk"], "reply:")
        self.assertEqual(second["chunk"], "hello")
        self.assertEqual(final["request_id"], request_id)
        self.assertTrue(final["ok"])
        self.assertEqual(final["response"], "reply:hello")
        worker.submit("close")
        self._next(worker, "close")
        worker.join(2)

    def test_stream_can_be_cancelled_without_persisting_partial_answer(self):
        worker_holder = {}
        closed = []

        class SlowRuntime(FakeRuntime):
            def stream_message(self, message):
                self._check_thread()
                try:
                    yield "partial"
                    while not worker_holder["worker"]._cancel_event.is_set():
                        time.sleep(0.01)
                finally:
                    closed.append(True)

        worker = DesktopRuntimeWorker(SlowRuntime)
        worker_holder["worker"] = worker
        worker.start()
        self._next(worker, "ready")
        worker.submit("stream", message="hello")
        chunk = self._next(worker, "chunk")
        self.assertEqual(chunk["chunk"], "partial")
        worker.submit("cancel")
        final = self._next(worker, "stream")
        self.assertTrue(final["cancelled"])
        self.assertFalse(final["ok"])
        self.assertEqual(final["response"], "partial")
        self.assertTrue(closed)
        worker.submit("close")
        self._next(worker, "close")
        worker.join(2)

    def test_sync_status_and_pair_are_worker_actions(self):
        sync_instances = []
        def sync_factory():
            instance = FakeSyncRuntime()
            sync_instances.append(instance)
            return instance

        worker = DesktopRuntimeWorker(FakeRuntime, sync_factory)
        worker.start()
        self._next(worker, "ready")
        worker.submit("sync_status")
        status = self._next(worker, "sync_status")
        self.assertIn("not paired", status["status"])
        worker.submit("sync_pair", code="one-time-code", device_label="Windows")
        paired = self._next(worker, "sync_pair")
        self.assertTrue(paired["ok"])
        self.assertIn("paired", paired["status"])
        worker.submit("close")
        self._next(worker, "close")
        worker.join(2)
        self.assertTrue(sync_instances[0].closed)

    def test_list_and_switch_are_dispatched_on_runtime_owner_thread(self):
        worker = DesktopRuntimeWorker(FakeRuntime)
        worker.start()
        self._next(worker, "ready")
        worker.submit("list")
        listed = self._next(worker, "list")
        self.assertEqual(listed["active_conversation_id"], "c1")
        self.assertEqual(listed["conversations"][0]["conversation_id"], "c1")
        worker.submit("switch", conversation_id="c2")
        switched = self._next(worker, "switch")
        self.assertTrue(switched["ok"])
        self.assertEqual(switched["conversation_id"], "c2")
        worker.submit("close")
        self._next(worker, "close")
        worker.join(2)

    def test_worker_exits_if_runtime_close_raises(self):
        class BrokenCloseRuntime(FakeRuntime):
            def close(self):
                self._check_thread()
                raise RuntimeError("cleanup failed")

        worker = DesktopRuntimeWorker(BrokenCloseRuntime)
        worker.start()
        self._next(worker, "ready")
        worker.submit("close")
        closed = self._next(worker, "close")
        self.assertFalse(closed["ok"])
        worker.join(2)
        self.assertFalse(worker._thread.is_alive())

    def test_sanitized_model_catalog_is_available_to_desktop(self):
        worker = DesktopRuntimeWorker(FakeRuntime)
        worker.start()
        self._next(worker, "ready")
        worker.submit("catalog")
        event = self._next(worker, "catalog")
        self.assertTrue(event["ok"])
        selected = event["catalog"][0]
        self.assertEqual(selected["configured_model"], "gpt-4.1-mini")
        self.assertEqual(selected["privacy"], "cloud_provider")
        self.assertNotIn("api_key", selected)
        worker.submit("close")
        self._next(worker, "close")
        worker.join(2)

    def test_provider_catalog_and_selection_are_worker_actions(self):
        worker = DesktopRuntimeWorker(FakeRuntime)
        worker.start()
        self._next(worker, "ready")
        worker.submit("providers")
        listed = self._next(worker, "providers")
        self.assertEqual(listed["selected"], "openai")
        self.assertEqual(len(listed["providers"]), 2)
        worker.submit("select_provider", name="openai")
        selected = self._next(worker, "select_provider")
        self.assertTrue(selected["ok"])
        self.assertEqual(selected["selected"], "openai")
        worker.submit("select_provider", name="anthropic")
        rejected = self._next(worker, "select_provider")
        self.assertFalse(rejected["ok"])
        worker.submit("close")
        self._next(worker, "close")
        worker.join(2)

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
