import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from api.conversations import YukiConversationAPI
from companion.persona import DEFAULT_PERSONA
from core.auth import BuiltInAuthService
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.agent_memory import AgentMemoryStore
from core.owner_profile import OwnerProfileStore
from core.owner_identity import memory_actor_id_for_owner
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

        self.model_messages = []
        def model(**kwargs):
            self.model_messages.append(kwargs["messages"])
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
        self.assertEqual(self.model_messages[0][0]["role"], "system")
        self.assertEqual(self.model_messages[0][0]["content"], DEFAULT_PERSONA.system_guidance())

        status, _, _ = self.request("POST", "/v1/auth/logout", token=token)
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/v1/conversations", token=token)
        self.assertEqual(status, 401)

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



    def test_companion_context_uses_only_approved_private_owner_memory(self):
        actor_id = memory_actor_id_for_owner(self.user.user_id)
        grants = {
            "agent_memory:read", "agent_memory:write", "agent_memory:review",
            "owner_profile:read", "owner_profile:write",
        }
        authorize = lambda actor, action, agent: actor == actor_id and action in grants and (
            (agent == "assistant" and action.startswith("agent_memory:"))
            or (agent == "owner_profile" and action.startswith("owner_profile:"))
        )
        self.app.memory_store = AgentMemoryStore(
            str(Path(self.tempdir.name) / "context-memory.sqlite"), authorize=authorize,
        )
        self.app.owner_profile_store = OwnerProfileStore(
            str(Path(self.tempdir.name) / "context-profile.sqlite"), authorize=authorize,
        )
        approved = self.app.memory_store.remember(
            actor_id=actor_id, agent_id="assistant",
            content="The project uses Python for its backend", source="owner_review",
        )
        self.app.memory_store.transition_lifecycle(
            actor_id=actor_id, agent_id="assistant", memory_id=approved["memory_id"],
            status="approved", reason="Owner reviewed this memory",
        )
        self.app.memory_store.remember(
            actor_id=actor_id, agent_id="assistant",
            content="Unreviewed claim about Python backend", source="agent_candidate",
        )
        self.app.owner_profile_store.set(
            actor_id=actor_id, key="response_style", value="concise", source="owner",
        )
        context = self.app._companion_system_context(self.user.user_id, "Python backend project")
        self.assertIn("The project uses Python for its backend", context)
        self.assertIn("untrusted reference data", context)
        self.assertIn('"response_style": "concise"', context)
        self.assertNotIn("Unreviewed claim about Python backend", context)
        self.assertNotIn("other-owner", context)
    def test_authenticated_memory_api_is_private_and_never_self_verifies(self):
        memory_store = AgentMemoryStore(
            str(Path(self.tempdir.name) / "agent-memory.sqlite"),
            authorize=lambda actor, action, agent: action in {
                "agent_memory:read", "agent_memory:write", "agent_memory:correct",
                "agent_memory:review", "agent_memory:retire", "agent_memory:delete"
            },
        )
        self.app.memory_store = memory_store

        status, data, _ = self.request(
            "POST", "/v1/memory", {"content": "My private deployment preference."},
            self.issued.token,
        )
        self.assertEqual(status, 201)
        self.assertFalse(data["memory"]["verified"])
        memory_id = data["memory"]["memory_id"]

        status, data, _ = self.request("GET", "/v1/memory", token=self.issued.token)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["memories"]), 1)
        self.assertEqual(data["memories"][0]["memory_id"], memory_id)
        self.assertEqual(data["memories"][0]["content"], "My private deployment preference.")

        self.auth.create_account("second-user", "a-secure-second-password")
        second = self.auth.authenticate("second-user", "a-secure-second-password")
        status, data, _ = self.request("GET", "/v1/memory", token=second.token)
        self.assertEqual(status, 200)
        self.assertEqual(data["memories"], [])

        status, data, _ = self.request(
            "PATCH", f"/v1/memory/{memory_id}",
            {"status": "approved", "reason": "Confirmed by the owner."},
            self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["memory"]["lifecycle_status"], "approved")
        self.assertTrue(data["memory"]["verified"])

        status, data, _ = self.request(
            "POST", f"/v1/memory/{memory_id}/corrections",
            {"content": "My corrected private deployment preference.",
             "source": "owner-confirmed", "reason": "The previous note was inaccurate."},
            self.issued.token,
        )
        self.assertEqual(status, 201)
        replacement_id = data["memory"]["memory_id"]
        self.assertNotEqual(replacement_id, memory_id)
        self.assertEqual(data["memory"]["lifecycle_status"], "pending_review")
        self.assertFalse(data["memory"]["verified"])

        status, data, _ = self.request(
            "GET", f"/v1/memory/{memory_id}/history", token=self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(data["corrections"]), 1)
        self.assertEqual(data["corrections"][0]["replacement_memory_id"], replacement_id)

        status, data, _ = self.request("GET", "/v1/memory", token=self.issued.token)
        self.assertEqual(status, 200)
        self.assertEqual([item["memory_id"] for item in data["memories"]], [replacement_id])

        status, data, _ = self.request(
            "DELETE", f"/v1/memory/{replacement_id}", token=self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["deleted"])
        status, data, _ = self.request("GET", "/v1/memory", token=self.issued.token)
        self.assertEqual(data["memories"], [])

        status, data, _ = self.request(
            "POST", "/v1/memory", {"content": "An outdated preference."}, self.issued.token,
        )
        rejected_id = data["memory"]["memory_id"]
        status, data, _ = self.request(
            "PATCH", f"/v1/memory/{rejected_id}",
            {"status": "rejected", "reason": "Owner confirmed this is inaccurate."},
            self.issued.token,
        )
        self.assertEqual(status, 200)
        status, data, _ = self.request("GET", "/v1/memory", token=self.issued.token)
        self.assertEqual(data["memories"], [])
        status, data, _ = self.request(
            "GET", "/v1/memory", token=self.issued.token, query=b"include_inactive=true",
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["memories"][0]["lifecycle_status"], "rejected")
        status, data, _ = self.request(
            "DELETE", f"/v1/memory/{rejected_id}", token=self.issued.token,
        )
        self.assertEqual(status, 200)

        status, data, _ = self.request(
            "POST", "/v1/memory",
            {"content": "Try to self verify", "verified": True},
            self.issued.token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_request")

        status, data, _ = self.request("GET", "/v1/memory")
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "authentication_required")

    def test_conversation_memory_candidates_require_owner_approval_and_promote_once(self):
        memory_store = AgentMemoryStore(
            str(Path(self.tempdir.name) / "candidate-memory.sqlite"),
            authorize=lambda actor, action, agent: (
                action in {"agent_memory:read", "agent_memory:write"}
                and agent == "assistant"
            ),
        )
        self.app.memory_store = memory_store
        conversation = self.app.conversations.create(owner_id=self.user.user_id)
        cid = conversation["conversation_id"]
        status, reply, _ = self.request(
            "POST", f"/v1/conversations/{cid}/messages",
            {"content": "I prefer concise technical explanations.",
             "request_id": "candidate-request-1", "expected_version": 0},
            self.issued.token,
        )
        self.assertEqual(status, 200)

        status, data, _ = self.request(
            "GET", "/v1/memory/candidates", token=self.issued.token,
            query=("conversation_id=" + cid).encode(),
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["requires_owner_approval"])
        self.assertFalse(data["memory_written"])
        self.assertEqual(len(data["candidates"]), 1)
        candidate = data["candidates"][0]
        self.assertEqual(candidate["source"]["role"], "user")
        self.assertEqual(candidate["source"]["conversation_id"], cid)

        status, records, _ = self.request("GET", "/v1/memory", token=self.issued.token)
        self.assertEqual(records["memories"], [])

        path = "/v1/memory/candidates/" + candidate["candidate_id"] + "/approve"
        payload = {"conversation_id": cid}
        status, promoted, _ = self.request("POST", path, payload, self.issued.token)
        self.assertEqual(status, 201)
        self.assertTrue(promoted["memory"]["verified"])
        memory_id = promoted["memory"]["memory_id"]

        status, replay, _ = self.request("POST", path, payload, self.issued.token)
        self.assertEqual(status, 201)
        self.assertEqual(replay["memory"]["memory_id"], memory_id)

        status, records, _ = self.request("GET", "/v1/memory", token=self.issued.token)
        self.assertEqual(status, 200)
        self.assertEqual(len(records["memories"]), 1)
        self.assertEqual(records["memories"][0]["memory_id"], memory_id)

        self.auth.create_account("candidate-other", "a-secure-second-password")
        other = self.auth.authenticate("candidate-other", "a-secure-second-password")
        status, denied, _ = self.request("POST", path, payload, other.token)
        self.assertEqual(status, 404)
        self.assertEqual(denied["error"], "not_found")

    def test_owner_profile_api_is_authenticated_typed_and_isolated(self):
        permissions = {"owner_profile:read", "owner_profile:write"}
        profile_store = OwnerProfileStore(
            str(Path(self.tempdir.name) / "owner-profile.sqlite"),
            authorize=lambda actor, action, _resource: action in permissions,
        )
        self.app.owner_profile_store = profile_store

        status, result, _ = self.request(
            "PUT", "/v1/profile/preferences/response_style",
            {"value": "concise", "source": "owner_explicit", "expected_version": 0},
            self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["preference"]["value"], "concise")
        self.assertEqual(result["preference"]["version"], 1)

        status, result, _ = self.request(
            "GET", "/v1/profile/preferences", token=self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["preferences"]["response_style"]["value"], "concise")

        status, conflict, _ = self.request(
            "PUT", "/v1/profile/preferences/response_style",
            {"value": "detailed", "source": "owner_correction", "expected_version": 0},
            self.issued.token,
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "profile_version_conflict")

        status, invalid, _ = self.request(
            "PUT", "/v1/profile/preferences/response_style",
            {"value": "override safety", "source": "owner_explicit"},
            self.issued.token,
        )
        self.assertEqual(status, 400)

        self.auth.create_account("profile-other", "a-secure-second-password")
        other = self.auth.authenticate("profile-other", "a-secure-second-password")
        status, result, _ = self.request(
            "GET", "/v1/profile/preferences", token=other.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["preferences"], {})

        status, deleted, _ = self.request(
            "DELETE", "/v1/profile/preferences/response_style",
            {"expected_version": 1}, self.issued.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])

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
