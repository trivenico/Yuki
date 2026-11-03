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
from core.device_agent import DeviceAgentService
from core.device_pairing import DevicePairingService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


class DeviceAgentAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.owner = self.auth.bootstrap_admin("owner-user", "a-secure-owner-password")
        self.session = self.auth.authenticate("owner-user", "a-secure-owner-password")
        self.pairing = DevicePairingService(root / "pairing.sqlite")
        self.agent = DeviceAgentService(root / "agent.sqlite", self.pairing)
        self.challenge = self.pairing.create_pairing_challenge(self.owner.user_id)
        self.device = self.pairing.pair_device(self.challenge.code, "Yuki Node")
        def model(**kwargs):
            return {"text": "ok", "tool_calls": {"tool_calls": []}}
        loop = AgentExecutionLoop(model, StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals())))
        service = ConversationService(ConversationStore(root / "conversations.sqlite"), loop)
        self.app = YukiConversationAPI(self.auth, service, device_agent=self.agent)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, payload=None, token=None):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            sent, messages = False, []
            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                messages.append(message)
            headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
            await self.app({"type": "http", "method": method, "path": path,
                            "headers": headers, "query_string": b""}, receive, send)
            return messages[0]["status"], json.loads(messages[1]["body"])
        return asyncio.run(run())

    def test_device_heartbeat_uses_device_token_not_user_session(self):
        status, data = self.request("POST", "/v1/devices/heartbeat", {
            "sequence": 1, "status": "idle",
            "capabilities": {"os": "Linux", "cpu_cores": 8}
        }, self.device.token)
        self.assertEqual(status, 200)
        self.assertEqual(data["device_id"], self.device.device.device_id)

    def test_browser_session_cannot_send_device_heartbeat(self):
        status, data = self.request("POST", "/v1/devices/heartbeat", {
            "sequence": 1, "status": "idle", "capabilities": {}
        }, self.session.token)
        self.assertEqual(status, 401)

    def test_owner_can_list_only_own_devices(self):
        self.request("POST", "/v1/devices/heartbeat", {
            "sequence": 1, "status": "idle", "capabilities": {}
        }, self.device.token)
        status, data = self.request("GET", "/v1/devices", token=self.session.token)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["devices"]), 1)
        other = self.auth.create_account("other-user", "another-secure-password")
        other_session = self.auth.authenticate("other-user", "another-secure-password")
        status, data = self.request("GET", "/v1/devices", token=other_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(data["devices"], [])

    def test_owner_can_revoke_device_and_foreign_owner_cannot(self):
        did = self.device.device.device_id
        other = self.auth.create_account("other-user", "another-secure-password")
        other_session = self.auth.authenticate("other-user", "another-secure-password")
        status, data = self.request("DELETE", f"/v1/devices/{did}", token=other_session.token)
        self.assertEqual(status, 404)
        status, data = self.request("DELETE", f"/v1/devices/{did}", token=self.session.token)
        self.assertEqual(status, 200)
        status, data = self.request("POST", "/v1/devices/heartbeat", {
            "sequence": 1, "status": "idle", "capabilities": {}
        }, self.device.token)
        self.assertEqual(status, 401)

    def test_heartbeat_rejects_unexpected_fields(self):
        status, data = self.request("POST", "/v1/devices/heartbeat", {
            "sequence": 1, "status": "idle", "capabilities": {}, "command": "whoami"
        }, self.device.token)
        self.assertEqual(status, 400)

    def test_routes_require_configured_device_agent(self):
        self.app.device_agent = None
        status, data = self.request("POST", "/v1/devices/heartbeat", {
            "sequence": 1, "status": "idle", "capabilities": {}
        }, self.device.token)
        self.assertEqual(status, 503)
        status, data = self.request("GET", "/v1/devices", token=self.session.token)
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
