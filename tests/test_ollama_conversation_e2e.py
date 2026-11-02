"""End-to-end contract tests for OllamaProvider + ConversationRuntime."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from automation.agent_sessions import AgentSessionStore
from automation.conversation_runtime import ConversationRuntime
from automation.conversation_service import ConversationService
from providers.ollama import OllamaProvider


class OllamaStubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    stream_lines = (
        {"response": "Yuki ", "done": False},
        {"response": "is online.", "done": False},
        {"response": "", "done": True},
    )
    truncate = False
    requests = []

    def log_message(self, *_args):
        pass

    def _send_json(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/api/tags":
            return self._send_json(404, {"error": "not found"})
        self._send_json(200, {"models": [{"name": "test-model:latest"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(body)
        if self.path != "/api/generate" or body.get("stream") is not True:
            return self._send_json(400, {"error": "unexpected request"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        lines = self.stream_lines[:-1] if self.truncate else self.stream_lines
        try:
            for event in lines:
                self.wfile.write(json.dumps(event).encode() + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class OllamaConversationE2ETests(unittest.TestCase):
    def setUp(self):
        OllamaStubHandler.requests = []
        OllamaStubHandler.truncate = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaStubHandler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = AgentSessionStore(
            Path(self.tmp.name) / "e2e.sqlite3",
            authorize=lambda actor, action, resource: actor == "test-user",
        )
        self.service = ConversationService(store)
        self.session = self.service.start(
            actor_id="test-user", idempotency_key="start",
            title="E2E", agent_id="general",
        )
        self.runtime = ConversationRuntime(self.service)
        self.provider = OllamaProvider(
            base_url=f"http://127.0.0.1:{self.server.server_port}",
            model="test-model", timeout=3,
        )

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _run(self, key="e2e"):
        return list(self.runtime.respond_stream(
            actor_id="test-user", session_id=self.session.session_id,
            idempotency_key=key, user_message="Introduce yourself",
            provider=self.provider,
        ))

    def test_live_provider_contract_persists_only_completed_answer(self):
        self.assertTrue(self.provider.is_available())
        events = self._run()
        self.assertEqual([event.type for event in events], ["delta", "delta", "completed"])
        self.assertEqual("".join(event.content for event in events[:2]), "Yuki is online.")
        history = self.service.history(
            actor_id="test-user", session_id=self.session.session_id,
        )
        assistant = [event for event in history if event.kind == "message.assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0].payload["content"], "Yuki is online.")
        self.assertTrue(OllamaStubHandler.requests[-1]["stream"])

    def test_truncated_provider_stream_never_persists_partial_answer(self):
        OllamaStubHandler.truncate = True
        from automation.conversation_runtime import ConversationGenerationError
        with self.assertRaises(ConversationGenerationError):
            self._run("truncated")
        history = self.service.history(
            actor_id="test-user", session_id=self.session.session_id,
        )
        self.assertFalse(any(event.kind == "message.assistant" for event in history))
        self.assertEqual(history[-1].kind, "generation.failed")


if __name__ == "__main__":
    unittest.main()
