import asyncio
import concurrent.futures
import json
import tempfile
import threading
import unittest
from pathlib import Path

from api.conversations import YukiConversationAPI
from core.auth import BuiltInAuthService
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


class ConversationAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.user = self.auth.bootstrap_admin("admin-user", "a-secure-test-password")
        self.issued = self.auth.authenticate("admin-user", "a-secure-test-password")

        def model(**kwargs):
            return {"text": "Hello from Yuki", "tool_calls": {"tool_calls": []}}

        protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
        loop = AgentExecutionLoop(model, protocol, stream_model=lambda **kwargs: iter([{'type': 'text_delta', 'text': 'Hello '}, {'type': 'text_delta', 'text': 'from Yuki'}]))
        service = ConversationService(ConversationStore(root / "conversations.sqlite"), loop)
        self.app = YukiConversationAPI(self.auth, service)

    def tearDown(self):
        self.tempdir.cleanup()

    def request(self, method, path, payload=None, token=None, query=b""):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            sent = False
            messages = []
            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                messages.append(message)
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            scope = {
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": query,
            }
            await self.app(scope, receive, send)
            status = messages[0]["status"]
            data = json.loads(messages[1]["body"])
            response_headers = dict(messages[0]["headers"])
            return status, data, response_headers
        return asyncio.run(run())

    def test_provider_catalog_requires_authentication_and_projects_safe_fields(self):
        self.app.provider_catalog = lambda: [{
            "name": "openai", "label": "OpenAI", "implemented": True,
            "ready": True, "privacy": "cloud_provider",
            "configured_model": "gpt-4.1-mini", "credential_configured": True,
            "selected": True, "status": "api_key_and_model_configured",
            "key_env": "OPENAI_API_KEY", "secret": "must-never-leak",
            "endpoint": "https://private.example",
        }]
        status, data, _ = self.request("GET", "/v1/providers")
        self.assertEqual(status, 401)
        status, data, _ = self.request("GET", "/v1/providers", token=self.issued.token)
        self.assertEqual(status, 200)
        self.assertEqual(data["providers"][0]["name"], "openai")
        self.assertTrue(data["providers"][0]["credential_configured"])
        self.assertNotIn("secret", data["providers"][0])
        self.assertNotIn("key_env", data["providers"][0])
        self.assertNotIn("endpoint", data["providers"][0])

    def test_provider_catalog_failure_is_generic(self):
        self.app.provider_catalog = lambda: (_ for _ in ()).throw(RuntimeError("private details"))
        status, data, _ = self.request("GET", "/v1/providers", token=self.issued.token)
        self.assertEqual(status, 500)
        self.assertEqual(data, {"error": "provider_catalog_unavailable"})

    def test_authenticated_identity_endpoint_returns_role(self):
        status, data, _ = self.request("GET", "/v1/auth/me", token=self.issued.token)
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["role"], "admin")
        self.assertEqual(data["user"]["username"], "admin-user")

    def test_provider_selection_requires_admin_and_explicit_scope_confirmation(self):
        calls = []
        self.app.provider_catalog = lambda: [
            {"name": "ollama", "privacy": "local_runtime", "implemented": True, "ready": True}
        ]
        self.app.provider_selector = lambda name: calls.append(name) or {
            "name": name, "label": "Ollama", "selected": True, "ready": True,
            "privacy": "local_runtime",
        }
        status, data, _ = self.request(
            "PUT", "/v1/providers/active",
            {"provider": "ollama", "confirm_global": True, "confirm_cloud": False},
            self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["provider"]["name"], "ollama")
        self.assertEqual(calls, ["ollama"])

        status, data, _ = self.request(
            "PUT", "/v1/providers/active",
            {"provider": "ollama"}, self.issued.token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "explicit_confirmation_required")
        self.assertEqual(calls, ["ollama"])

    def test_non_admin_cannot_change_server_wide_provider(self):
        self.app.provider_selector = lambda name: self.fail("selector must not be called")
        self.auth.create_account("regular-user", "a-secure-user-password")
        session = self.auth.authenticate("regular-user", "a-secure-user-password")
        status, data, _ = self.request(
            "PUT", "/v1/providers/active",
            {"provider": "ollama", "confirm_global": True, "confirm_cloud": False},
            session.token,
        )
        self.assertEqual(status, 403)
        self.assertEqual(data["error"], "administrator_required")

    def test_cloud_provider_selection_requires_cloud_consent(self):
        calls = []
        self.app.provider_catalog = lambda: [
            {"name": "openai", "privacy": "cloud_provider", "implemented": True, "ready": True}
        ]
        self.app.provider_selector = lambda name: calls.append(name) or {"name": name}
        status, data, _ = self.request(
            "PUT", "/v1/providers/active",
            {"provider": "openai", "confirm_global": True, "confirm_cloud": False},
            self.issued.token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "cloud_consent_required")
        self.assertEqual(calls, [])

    def test_non_loopback_ollama_endpoint_requires_cloud_consent(self):
        calls = []
        self.app.provider_catalog = lambda: [
            {"name": "ollama", "privacy": "local_or_configured_endpoint",
             "implemented": True, "ready": True}
        ]
        self.app.provider_selector = lambda name: calls.append(name) or {"name": name}
        status, data, _ = self.request(
            "PUT", "/v1/providers/active",
            {"provider": "ollama", "confirm_global": True, "confirm_cloud": False},
            self.issued.token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "cloud_consent_required")
        self.assertEqual(calls, [])

    def test_login_create_send_history_and_logout(self):
        status, data, headers = self.request(
            "POST", "/v1/auth/login",
            {"username": "admin-user", "password": "a-secure-test-password"},
        )
        self.assertEqual(status, 200)
        token = data["access_token"]
        self.assertEqual(headers[b"cache-control"], b"no-store")

        status, conversation, _ = self.request(
            "POST", "/v1/conversations", {"title": "API test"}, token
        )
        self.assertEqual(status, 201)
        cid = conversation["conversation_id"]

        status, reply, _ = self.request(
            "POST", f"/v1/conversations/{cid}/messages",
            {"content": "Hello", "request_id": "api-request-1", "expected_version": 0},
            token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(reply["answer"], "Hello from Yuki")

        status, history, _ = self.request("GET", f"/v1/conversations/{cid}/messages", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([item["role"] for item in history["messages"]], ["user", "assistant"])

        status, _, _ = self.request("POST", "/v1/auth/logout", token=token)
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/v1/conversations", token=token)
        self.assertEqual(status, 401)

    def test_authenticated_sessions_select_distinct_owner_execution_loops(self):
        self.auth.create_account("second-user", "another-secure-password")
        second_session = self.auth.authenticate("second-user", "another-secure-password")
        second_user_id = second_session.user.user_id
        first_user_id = self.issued.user.user_id
        resolved = []

        def owner_factory(owner_id):
            resolved.append(owner_id)
            def model(**kwargs):
                return {"text": "response-for-" + owner_id, "tool_calls": {"tool_calls": []}}
            protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
            return AgentExecutionLoop(model, protocol)

        self.app.conversations.agent_loop_for_owner = owner_factory
        status, first_conversation, _ = self.request(
            "POST", "/v1/conversations", {"title": "First"}, self.issued.token
        )
        self.assertEqual(status, 201)
        status, second_conversation, _ = self.request(
            "POST", "/v1/conversations", {"title": "Second"}, second_session.token
        )
        self.assertEqual(status, 201)

        status, first_reply, _ = self.request(
            "POST", f"/v1/conversations/{first_conversation['conversation_id']}/messages",
            {"content": "hello", "request_id": "first-owner-request", "expected_version": 0},
            self.issued.token,
        )
        self.assertEqual(status, 200)
        status, second_reply, _ = self.request(
            "POST", f"/v1/conversations/{second_conversation['conversation_id']}/messages",
            {"content": "hello", "request_id": "second-owner-request", "expected_version": 0},
            second_session.token,
        )
        self.assertEqual(status, 200)

        self.assertEqual(resolved, [first_user_id, second_user_id])
        self.assertEqual(first_reply["answer"], "response-for-" + first_user_id)
        self.assertEqual(second_reply["answer"], "response-for-" + second_user_id)

        # A valid session cannot override the identity used by the owner factory.
        status, _, _ = self.request(
            "POST", "/v1/conversations",
            {"title": "Forged", "owner_id": second_user_id}, self.issued.token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(resolved, [first_user_id, second_user_id])

    def test_distinct_authenticated_users_generate_concurrently_without_loop_leakage(self):
        self.auth.create_account("parallel-user", "parallel-secure-password")
        second = self.auth.authenticate("parallel-user", "parallel-secure-password")
        barrier = threading.Barrier(2)
        entered = []
        entered_lock = threading.Lock()

        def owner_factory(owner_id):
            def model(**kwargs):
                with entered_lock:
                    entered.append(owner_id)
                barrier.wait(timeout=5)
                return {"text": "parallel-response-" + owner_id, "tool_calls": {"tool_calls": []}}
            return AgentExecutionLoop(
                model, StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
            )

        self.app.conversations.agent_loop_for_owner = owner_factory
        first_conversation = self.app.conversations.create(owner_id=self.issued.user.user_id)
        second_conversation = self.app.conversations.create(owner_id=second.user.user_id)

        def send(owner_token, conversation, request_id):
            return self.request(
                "POST", f"/v1/conversations/{conversation['conversation_id']}/messages",
                {"content": "run concurrently", "request_id": request_id, "expected_version": 0},
                owner_token,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(send, self.issued.token, first_conversation, "parallel-first")
            second_future = pool.submit(send, second.token, second_conversation, "parallel-second")
            first_status, first_reply, _ = first_future.result(timeout=10)
            second_status, second_reply, _ = second_future.result(timeout=10)

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(first_reply["status"], "completed")
        self.assertEqual(second_reply["status"], "completed")
        self.assertEqual(set(entered), {self.issued.user.user_id, second.user.user_id})
        self.assertEqual(first_reply["answer"], "parallel-response-" + self.issued.user.user_id)
        self.assertEqual(second_reply["answer"], "parallel-response-" + second.user.user_id)

    def test_unauthenticated_and_forged_owner_fields_are_rejected(self):
        status, _, _ = self.request("GET", "/v1/conversations")
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "POST", "/v1/conversations",
            {"title": "Not allowed", "owner_id": "victim"},
            self.issued.token,
        )
        self.assertEqual(status, 400)

    def test_invalid_login_has_generic_error(self):
        status, data, _ = self.request(
            "POST", "/v1/auth/login",
            {"username": "admin-user", "password": "incorrect-password"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(data, {"error": "invalid_credentials"})

    def test_unknown_conversation_does_not_leak_existence(self):
        status, data, _ = self.request(
            "GET", "/v1/conversations/00000000-0000-4000-8000-000000000000/messages",
            token=self.issued.token,
        )
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "not_found"})

    def test_oversized_body_is_rejected(self):
        async def run():
            events = [
                {"type": "http.request", "body": b"x" * 24_001, "more_body": False}
            ]
            async def receive():
                return events.pop(0)
            sent = []
            async def send(message):
                sent.append(message)
            await self.app(
                {"type": "http", "method": "POST", "path": "/v1/auth/login",
                 "headers": [], "query_string": b""},
                receive, send,
            )
            return sent[0]["status"]
        self.assertEqual(asyncio.run(run()), 400)


    def test_authenticated_stream_endpoint_emits_sse_and_persists_reply(self):
        conversation = self.app.conversations.create(owner_id=self.user.user_id)
        cid = conversation["conversation_id"]
        async def run():
            body = json.dumps({
                "content": "Stream please", "request_id": "stream-request-1",
                "expected_version": 0,
            }).encode()
            events = [{"type": "http.request", "body": body, "more_body": False}]
            sent = []
            async def receive():
                if events:
                    return events.pop(0)
                await asyncio.Future()
            async def send(message):
                sent.append(message)
            await self.app(
                {"type": "http", "method": "POST",
                 "path": f"/v1/conversations/{cid}/messages/stream",
                 "headers": [(b"authorization", f"Bearer {self.issued.token}".encode())],
                 "query_string": b""},
                receive, send,
            )
            return sent
        sent = asyncio.run(run())
        self.assertEqual(sent[0]["status"], 200)
        headers = dict(sent[0]["headers"])
        self.assertEqual(headers[b"content-type"], b"text/event-stream; charset=utf-8")
        stream = b"".join(item.get("body", b"") for item in sent[1:]).decode()
        self.assertIn("event: delta\n", stream)
        self.assertIn('"text":"Hello "', stream)
        self.assertIn("event: completed\n", stream)
        history = self.app.conversations.history(
            owner_id=self.user.user_id, conversation_id=cid
        )
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[-1]["content"], "Hello from Yuki")


    def test_authenticated_cancel_endpoint_only_cancels_active_owned_generation(self):
        conversation = self.app.conversations.create(owner_id=self.user.user_id)
        cid = conversation["conversation_id"]
        cancellation = threading.Event()
        key = (self.user.user_id, cid, "cancel-me")
        with self.app._active_lock:
            self.app._active_generations[key] = cancellation
        status, data, _ = self.request(
            "POST", f"/v1/conversations/{cid}/messages/cancel",
            {"request_id": "cancel-me"}, self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(data, {"cancel_requested": True})
        self.assertTrue(cancellation.is_set())
        with self.app._active_lock:
            self.app._active_generations.pop(key, None)

    def test_cancel_endpoint_does_not_cancel_another_users_generation(self):
        other = self.app.conversations.create(owner_id="another-owner")
        cid = other["conversation_id"]
        status, data, _ = self.request(
            "POST", f"/v1/conversations/{cid}/messages/cancel",
            {"request_id": "guess"}, self.issued.token,
        )
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "not_found"})

if __name__ == "__main__":
    unittest.main()
