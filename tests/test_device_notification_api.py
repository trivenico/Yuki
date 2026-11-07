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
from core.device_notification_outbox import DeviceNotificationOutbox
from core.device_pairing import DevicePairingService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


class DeviceNotificationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.user = self.auth.bootstrap_admin("device-api-user", "a-secure-test-password")
        self.session = self.auth.authenticate("device-api-user", "a-secure-test-password")
        protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
        loop = AgentExecutionLoop(lambda **kwargs: {"text": "ok", "tool_calls": {"tool_calls": []}}, protocol)
        conversations = ConversationService(ConversationStore(root / "conversations.sqlite"), loop)
        self.pairing = DevicePairingService(root / "pairing.sqlite")
        self.outbox = DeviceNotificationOutbox(root / "outbox.sqlite", self.pairing)
        challenge = self.pairing.create_pairing_challenge(self.user.user_id)
        self.device = self.pairing.pair_device(challenge.code, "Yuki Desktop")
        self.app = YukiConversationAPI(
            self.auth, conversations, notification_outbox=self.outbox
        )

    def request(self, method, path, token):
        async def run():
            messages = []
            sent = False
            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                messages.append(message)
            scope = {
                "type": "http", "method": method, "path": path,
                "headers": [(b"authorization", f"Bearer {token}".encode())],
                "query_string": b"",
            }
            await self.app(scope, receive, send)
            return messages[0]["status"], json.loads(messages[1]["body"])
        return asyncio.run(run())

    def test_feed_rejects_browser_session_and_accepts_device_credential(self):
        event = self.outbox.enqueue(
            owner_id=self.user.user_id, device_id=self.device.device.device_id,
            event_type="companion.reminder_due", payload={"title": "Reminder"},
        )
        status, _ = self.request("GET", "/v1/devices/notifications", self.session.token)
        self.assertEqual(status, 401)
        status, data = self.request("GET", "/v1/devices/notifications", self.device.token)
        self.assertEqual(status, 200)
        self.assertEqual([item["event_id"] for item in data["events"]], [event.event_id])
        self.assertEqual(data["next_cursor"], event.sequence)

    def test_ack_is_device_scoped_and_idempotent(self):
        event = self.outbox.enqueue(
            owner_id=self.user.user_id, device_id=self.device.device.device_id,
            event_type="companion.notice", payload={"title": "Hello"},
        )
        path = f"/v1/devices/notifications/{event.event_id}/ack"
        self.assertEqual(self.request("POST", path, self.session.token)[0], 401)
        self.assertEqual(self.request("POST", path, self.device.token),
                         (200, {"acknowledged": True}))
        self.assertEqual(self.request("POST", path, self.device.token),
                         (200, {"acknowledged": True}))
        status, data = self.request("GET", "/v1/devices/notifications", self.device.token)
        self.assertEqual(status, 200)
        self.assertEqual(data["events"], [])

    def test_feed_rejects_invalid_cursor_parameters(self):
        async def run():
            messages = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                messages.append(message)
            scope = {
                "type": "http", "method": "GET", "path": "/v1/devices/notifications",
                "headers": [(b"authorization", f"Bearer {self.device.token}".encode())],
                "query_string": b"limit=101",
            }
            await self.app(scope, receive, send)
            return messages[0]["status"], json.loads(messages[1]["body"])
        status, data = asyncio.run(run())
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_pagination")

    def test_revoked_device_cannot_poll_or_acknowledge(self):
        event = self.outbox.enqueue(
            owner_id=self.user.user_id, device_id=self.device.device.device_id,
            event_type="companion.notice", payload={},
        )
        self.pairing.revoke_device(self.user.user_id, self.device.device.device_id)
        status, _ = self.request("GET", "/v1/devices/notifications", self.device.token)
        self.assertEqual(status, 401)
        path = f"/v1/devices/notifications/{event.event_id}/ack"
        self.assertEqual(self.request("POST", path, self.device.token)[0], 401)


if __name__ == "__main__":
    unittest.main()
