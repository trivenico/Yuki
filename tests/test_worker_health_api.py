import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.worker_health import AuthenticatedWorkerHealthAPI
from automation.worker_registry import WorkerRegistry


class _Auth:
    def resolve_session(self, token):
        if token == "admin-token":
            return SimpleNamespace(role="admin", user_id="admin")
        if token == "user-token":
            return SimpleNamespace(role="user", user_id="user")
        return None


class _App:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})


class WorkerHealthAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = WorkerRegistry(str(Path(self.tmp.name) / "workers.sqlite3"))
        self.registry.register("worker-a", metadata={"role": "specialist"})
        self.app = _App()
        self.api = AuthenticatedWorkerHealthAPI(self.app, _Auth(), self.registry)

    def request(self, path="/v1/admin/workers", token=None, method="GET"):
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        headers = [] if token is None else [(b"authorization", ("Bearer " + token).encode())]
        asyncio.run(self.api({
            "type": "http", "method": method, "path": path, "headers": headers,
        }, receive, send))
        return sent

    def test_requires_authentication(self):
        sent = self.request()
        self.assertEqual(sent[0]["status"], 401)

    def test_requires_administrator(self):
        sent = self.request(token="user-token")
        self.assertEqual(sent[0]["status"], 403)

    def test_admin_receives_sanitized_worker_health(self):
        sent = self.request(token="admin-token")
        self.assertEqual(sent[0]["status"], 200)
        payload = json.loads(sent[1]["body"])
        self.assertEqual(payload["workers"][0]["worker_id"], "worker-a")
        self.assertEqual(payload["workers"][0]["metadata"]["role"], "specialist")
        self.assertNotIn("session_token", sent[1]["body"].decode())
        self.assertNotIn("session_hash", sent[1]["body"].decode())

    def test_other_paths_delegate_to_wrapped_application(self):
        self.request(path="/v1/auth/me", token="admin-token")
        self.assertEqual(self.app.calls, 1)

    def test_head_does_not_return_body(self):
        sent = self.request(token="admin-token", method="HEAD")
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(sent[1]["body"], b"")


if __name__ == "__main__":
    unittest.main()
