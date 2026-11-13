"""Authenticated HTTP integration coverage for per-owner provider execution."""
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _DenyAll:
    def authorize_tool(self, name):
        return False


def _loop_for(answer):
    return AgentExecutionLoop(
        lambda **kwargs: {"text": answer, "tool_calls": {"tool_calls": []}},
        StructuredToolProtocol(GuardedToolRuntime({}, _DenyAll())),
    )


class AuthenticatedOwnerRuntimeHTTPTests(unittest.TestCase):
    def test_concurrent_authenticated_owners_reach_only_their_execution_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(
                str(root / "auth.sqlite3"), password_iterations=100_000,
            )
            alice = auth.create_account("alice-user", "alice-secure-password")
            bob = auth.create_account("bob-user", "bob-secure-password")
            alice_session = auth.authenticate("alice-user", "alice-secure-password")
            bob_session = auth.authenticate("bob-user", "bob-secure-password")

            barrier = threading.Barrier(2)
            factory_calls = []
            factory_lock = threading.Lock()

            def owner_factory(owner_id):
                with factory_lock:
                    factory_calls.append(owner_id)

                def model(**kwargs):
                    barrier.wait(timeout=10)
                    return {
                        "text": "private-answer:" + owner_id,
                        "tool_calls": {"tool_calls": []},
                    }

                return AgentExecutionLoop(
                    model, StructuredToolProtocol(GuardedToolRuntime({}, _DenyAll())),
                )

            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth,
                agent_loop=_loop_for("shared-fallback-must-not-run"),
                agent_loop_for_owner=owner_factory,
                tools_for_user=lambda user: [],
            )

            async def request(method, path, *, token, payload=None):
                body = json.dumps(payload).encode("utf-8") if payload is not None else b""
                sent = []
                consumed = False

                async def receive():
                    nonlocal consumed
                    if consumed:
                        return {"type": "http.disconnect"}
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}

                async def send(message):
                    sent.append(message)

                await runtime.app(
                    {
                        "type": "http",
                        "method": method,
                        "path": path,
                        "headers": [(b"authorization", ("Bearer " + token).encode())],
                        "query_string": b"",
                    },
                    receive,
                    send,
                )
                return sent[0]["status"], json.loads(sent[1]["body"])

            async def scenario():
                alice_status, alice_conversation = await request(
                    "POST", "/v1/conversations", token=alice_session.token,
                    payload={"title": "Alice private"},
                )
                bob_status, bob_conversation = await request(
                    "POST", "/v1/conversations", token=bob_session.token,
                    payload={"title": "Bob private"},
                )
                self.assertEqual((alice_status, bob_status), (201, 201))

                alice_id = alice_conversation["conversation_id"]
                bob_id = bob_conversation["conversation_id"]
                alice_result, bob_result = await asyncio.gather(
                    request(
                        "POST", f"/v1/conversations/{alice_id}/messages",
                        token=alice_session.token,
                        payload={"content": "hello", "request_id": "alice-turn", "expected_version": 0},
                    ),
                    request(
                        "POST", f"/v1/conversations/{bob_id}/messages",
                        token=bob_session.token,
                        payload={"content": "hello", "request_id": "bob-turn", "expected_version": 0},
                    ),
                )
                return alice_id, bob_id, alice_result, bob_result

            try:
                alice_id, bob_id, alice_result, bob_result = asyncio.run(scenario())
                self.assertEqual(alice_result[0], 200, alice_result)
                self.assertEqual(bob_result[0], 200, bob_result)
                self.assertEqual(alice_result[1]["answer"], "private-answer:" + alice.user_id)
                self.assertEqual(bob_result[1]["answer"], "private-answer:" + bob.user_id)
                self.assertCountEqual(factory_calls, [alice.user_id, bob.user_id])

                # A valid session for Bob cannot read Alice's conversation.
                status, body = asyncio.run(request(
                    "GET", f"/v1/conversations/{alice_id}/messages",
                    token=bob_session.token,
                ))
                self.assertEqual(status, 404)
                self.assertEqual(body, {"error": "not_found"})
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
