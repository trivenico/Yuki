"""Tests for the authenticated ChatGPT MCP bridge."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from integrations.chatgpt_mcp import BridgeConfigError, ConversationStore, _handle_rpc, _load_config, create_handler
from providers.mock import MockProvider
from yuki_agent import YukiAgent
from yuki_providers import ProviderManager


class FakeAgent:
    def __init__(self):
        self.prompts = []

    def status(self):
        return "Provider is ready."

    def ask(self, prompt):
        self.prompts.append(prompt)
        return "Yuki reply: " + prompt


class MCPBridgeTests(unittest.TestCase):
    def setUp(self):
        self.token = "t" * 48
        self.agent = FakeAgent()
        self.providers = ProviderManager()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            create_handler(self.agent, self.providers, token=self.token),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, payload=None, token=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        req = Request(self.base_url + path, data=data, headers=headers)
        return urlopen(req, timeout=3)

    def test_health_endpoint(self):
        with self.request("/health") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")

    def test_unauthenticated_mcp_request_is_rejected(self):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        with self.assertRaises(HTTPError) as error:
            self.request("/mcp", payload)
        self.assertEqual(error.exception.code, 401)

    def test_chat_calls_actual_agent_and_returns_response(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "yuki_chat", "arguments": {"message": "hello"}},
        }
        with self.request("/mcp", payload, self.token) as response:
            result = json.loads(response.read())
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["result"]["content"][0]["text"], "Yuki reply: hello")
        self.assertEqual(self.agent.prompts, ["hello"])

    def test_only_chat_and_status_tools_are_exposed(self):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        with self.request("/mcp", payload, self.token) as response:
            result = json.loads(response.read())
        self.assertEqual({item["name"] for item in result["result"]["tools"]},
                         {"yuki_chat", "yuki_status", "yuki_delete_session"})

    def test_prompt_length_is_bounded(self):
        payload = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "yuki_chat", "arguments": {"message": "x" * 12001}},
        }
        with self.request("/mcp", payload, self.token) as response:
            result = json.loads(response.read())
        self.assertTrue(result["result"]["isError"])
        self.assertEqual(self.agent.prompts, [])

    def test_missing_mcp_accept_headers_is_rejected(self):
        req = Request(
            self.base_url + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(req, timeout=3)
        self.assertEqual(error.exception.code, 406)

    def test_oversized_batch_is_rejected(self):
        payload = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(11)]
        with self.assertRaises(HTTPError) as error:
            self.request("/mcp", payload, self.token)
        self.assertEqual(error.exception.code, 400)

    def test_conversation_store_is_bounded_and_deletable(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(os.path.join(temp_dir, "history.sqlite3"),
                                      retention_seconds=3600, max_sessions=3, max_turns=2)
            store.append("session_123", "first", "one")
            store.append("session_123", "second", "two")
            store.append("session_123", "third", "three")
            self.assertEqual(store.history("session_123"), [("second", "two"), ("third", "three")])
            store.delete("session_123")
            self.assertEqual(store.history("session_123"), [])

    def test_memory_disabled_delete_tool_does_not_claim_data_was_deleted(self):
        payload = {
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "yuki_delete_session", "arguments": {"session_id": "session_123"}},
        }
        with self.request("/mcp", payload, self.token) as response:
            result = json.loads(response.read())
        self.assertTrue(result["result"]["isError"])
        self.assertIn("memory is disabled", result["result"]["content"][0]["text"].lower())

    def test_chat_capacity_returns_retryable_error(self):
        from threading import BoundedSemaphore
        slots = BoundedSemaphore(1)
        self.assertTrue(slots.acquire())
        payload = {
            "jsonrpc": "2.0", "id": 17, "method": "tools/call",
            "params": {"name": "yuki_chat", "arguments": {"message": "hello"}},
        }
        try:
            response = _handle_rpc(payload, self.agent, self.providers, chat_slots=slots)
            self.assertTrue(response["result"]["isError"])
            self.assertIn("at capacity", response["result"]["content"][0]["text"].lower())
            self.assertEqual(self.agent.prompts, [])
        finally:
            slots.release()

    def test_chat_capacity_is_released_when_agent_raises(self):
        from threading import BoundedSemaphore

        class RaisingAgent(FakeAgent):
            def ask(self, prompt):
                raise RuntimeError("provider failure")

        slots = BoundedSemaphore(1)
        payload = {
            "jsonrpc": "2.0", "id": 18, "method": "tools/call",
            "params": {"name": "yuki_chat", "arguments": {"message": "hello"}},
        }
        response = _handle_rpc(payload, RaisingAgent(), self.providers, chat_slots=slots)
        self.assertTrue(response["result"]["isError"])
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()

    def test_invalid_remote_binding_configuration_fails_closed(self):
        with self.assertRaises(BridgeConfigError):
            _load_config({"YUKI_MCP_BEARER_TOKEN": "x" * 48, "YUKI_MCP_HOST": "0.0.0.0"})


if __name__ == "__main__":
    unittest.main()
