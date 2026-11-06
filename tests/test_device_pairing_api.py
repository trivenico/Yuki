import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.conversations import YukiConversationAPI
from core.auth import BuiltInAuthService
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.device_pairing import DevicePairingService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


class DevicePairingAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.user = self.auth.bootstrap_admin("pair-api-user", "a-secure-test-password")
        self.session = self.auth.authenticate("pair-api-user", "a-secure-test-password")
        protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
        loop = AgentExecutionLoop(
            lambda **kwargs: {"text": "ok", "tool_calls": {"tool_calls": []}}, protocol
        )
        conversations = ConversationService(ConversationStore(root / "chat.sqlite"), loop)
        self.pairing = DevicePairingService(root / "pairing.sqlite")
        self.app = YukiConversationAPI(
            self.auth, conversations, device_pairing=self.pairing
        )

    def request(self, method, path, token=None, payload=None, *, scheme="https", client=("198.51.100.10", 4000)):
        async def run():
            messages = []
            sent = False
            body = json.dumps(payload or {}).encode()
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
                "type": "http", "method": method, "path": path, "headers": headers,
                "query_string": b"", "scheme": scheme, "client": client,
            }
            await self.app(scope, receive, send)
            return messages[0]["status"], json.loads(messages[1]["body"])
        return asyncio.run(run())

    def test_challenge_requires_owner_session_and_returns_one_time_code(self):
        status, _ = self.request("POST", "/v1/devices/pairing-challenges")
        self.assertEqual(status, 401)
        status, data = self.request(
            "POST", "/v1/devices/pairing-challenges", self.session.token
        )
        self.assertEqual(status, 201)
        self.assertGreaterEqual(len(data["code"]), 32)
        self.assertIn("expires_at", data)

    def test_pairing_requires_secure_transport_for_remote_client(self):
        challenge = self.pairing.create_pairing_challenge(self.user.user_id)
        status, data = self.request(
            "POST", "/v1/devices/pair",
            payload={"code": challenge.code, "name": "Remote Desktop"},
            scheme="http", client=("198.51.100.20", 4000),
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "secure_transport_required")
        # Rejected insecure transport must not consume the single-use challenge.
        status, data = self.request(
            "POST", "/v1/devices/pair",
            payload={"code": challenge.code, "name": "Remote Desktop"},
            scheme="https", client=("198.51.100.20", 4000),
        )
        self.assertEqual(status, 201)
        self.assertEqual(data["device"]["name"], "Remote Desktop")
        self.assertTrue(data["device_token"])

    def test_loopback_pairing_is_allowed_and_code_is_single_use(self):
        challenge = self.pairing.create_pairing_challenge(self.user.user_id)
        payload = {"code": challenge.code, "name": "Yuki Desktop"}
        status, data = self.request(
            "POST", "/v1/devices/pair", payload=payload,
            scheme="http", client=("127.0.0.1", 50000),
        )
        self.assertEqual(status, 201)
        self.assertTrue(data["device_token"])
        status, data = self.request(
            "POST", "/v1/devices/pair", payload=payload,
            scheme="https", client=("127.0.0.1", 50000),
        )
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "invalid_or_expired_pairing_code")

    def test_challenge_creation_rejects_unexpected_body(self):
        status, data = self.request(
            "POST", "/v1/devices/pairing-challenges",
            self.session.token, payload={"owner_id": "attacker"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
