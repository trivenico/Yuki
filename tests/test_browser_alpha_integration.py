"""End-to-end local browser/API integration tests with a deterministic provider."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.runtime import build_conversation_runtime
from api.web import YukiWebApplication
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


def _agent_loop():
    def model(**kwargs):
        return {"text": "Local alpha response", "tool_calls": {"tool_calls": []}}

    protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
    return AgentExecutionLoop(model, protocol)


class BrowserAlphaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth = BuiltInAuthService(
            str(self.root / "auth.sqlite3"), password_iterations=100_000,
        )
        self.auth.bootstrap_admin("alpha-admin", "a-secure-alpha-password")
        self.config = {
            "storage_backend": "sqlite",
            "workspace": str(self.root),
        }
        self.runtime = build_conversation_runtime(
            self.config, auth_service=self.auth, agent_loop=_agent_loop(),
        )
        self.addCleanup(self.runtime.close)
        self.web = YukiWebApplication(self.runtime.web)

    def request(self, method, path, payload=None, token=None, *, host="127.0.0.1:8000"):
        async def invoke():
            body = json.dumps(payload).encode("utf-8") if payload is not None else b""
            delivered = False
            sent = []

            async def receive():
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                sent.append(message)

            headers = [(b"host", host.encode("ascii"))]
            if token:
                headers.append((b"authorization", ("Bearer " + token).encode("ascii")))
            await self.web(
                {"type": "http", "method": method, "path": path,
                 "headers": headers, "query_string": b""},
                receive, send,
            )
            response_headers = dict(sent[0].get("headers", []))
            response_body = sent[1].get("body", b"") if len(sent) > 1 else b""
            try:
                data = json.loads(response_body) if response_body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = response_body
            return sent[0]["status"], data, response_headers

        return asyncio.run(invoke())

    def test_browser_boundary_login_chat_history_and_logout(self):
        status, _, _ = self.request("GET", "/")
        self.assertEqual(status, 200)

        status, login, headers = self.request(
            "POST", "/v1/auth/login",
            {"username": "alpha-admin", "password": "a-secure-alpha-password"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        token = login["access_token"]

        status, created, _ = self.request(
            "POST", "/v1/conversations", {"title": "Local alpha"}, token,
        )
        self.assertEqual(status, 201)
        conversation_id = created["conversation_id"]

        status, reply, _ = self.request(
            "POST", f"/v1/conversations/{conversation_id}/messages",
            {"content": "Test local browser chat", "request_id": "browser-alpha-1",
             "expected_version": 0},
            token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(reply["answer"], "Local alpha response")

        status, history, _ = self.request(
            "GET", f"/v1/conversations/{conversation_id}/messages", token=token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [message["role"] for message in history["messages"]],
            ["user", "assistant"],
        )

        status, _, _ = self.request("POST", "/v1/auth/logout", token=token)
        self.assertEqual(status, 200)
        status, data, _ = self.request("GET", "/v1/conversations", token=token)
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "authentication_required")

    def test_untrusted_host_is_rejected_before_api_dispatch(self):
        status, data, _ = self.request(
            "POST", "/v1/auth/login",
            {"username": "alpha-admin", "password": "a-secure-alpha-password"},
            host="attacker.example",
        )
        self.assertEqual(status, 421)
        self.assertEqual(data, {"error": "untrusted_host"})

    def test_conversation_survives_runtime_reconstruction(self):
        session = self.auth.authenticate("alpha-admin", "a-secure-alpha-password")
        status, created, _ = self.request(
            "POST", "/v1/conversations", {"title": "Persistent alpha"},
            session.token,
        )
        self.assertEqual(status, 201)
        conversation_id = created["conversation_id"]
        self.runtime.close()

        replacement = build_conversation_runtime(
            self.config, auth_service=self.auth, agent_loop=_agent_loop(),
        )
        self.addCleanup(replacement.close)
        replacement_web = YukiWebApplication(replacement.web)

        async def invoke():
            sent = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                sent.append(message)
            await replacement_web(
                {"type": "http", "method": "GET",
                 "path": f"/v1/conversations/{conversation_id}/messages",
                 "headers": [
                     (b"host", b"127.0.0.1:8000"),
                     (b"authorization", ("Bearer " + session.token).encode("ascii")),
                 ],
                 "query_string": b""},
                receive, send,
            )
            return sent[0]["status"]

        self.assertEqual(asyncio.run(invoke()), 200)


if __name__ == "__main__":
    unittest.main()
