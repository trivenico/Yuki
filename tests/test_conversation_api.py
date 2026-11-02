import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from api.conversation_server import build_application, serve


class FakeProvider:
    def is_available(self): return True
    def stream(self, prompt):
        yield "Hello "
        yield "from Yuki."


class FakeManager:
    def create_routed_adapter(self, **kwargs):
        assert kwargs["allow_cloud"] is False
        return FakeProvider()


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth, handler = build_application(self.temp.name, FakeManager())
        self.auth.bootstrap_admin("admin-user", "correct-horse-battery-staple")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = "http://127.0.0.1:%s" % self.server.server_port
        self.token = self.request("POST", "/v1/auth/login",
            {"username":"admin-user","password":"correct-horse-battery-staple"})["access_token"]

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, token=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        extra = dict(headers or {})
        if token: extra["Authorization"] = "Bearer " + token
        if data is not None: extra["Content-Type"] = "application/json"
        req = Request(self.base + path, data=data, headers=extra, method=method)
        with urlopen(req, timeout=3) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            return raw.decode() if content_type.startswith("text/event-stream") else json.loads(raw)

    def test_login_session_and_authenticated_identity(self):
        identity = self.request("GET", "/v1/me", token=self.token)
        self.assertEqual(identity["username"], "admin-user")
        self.assertEqual(identity["role"], "admin")

    def test_unauthenticated_conversation_access_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("GET", "/v1/conversations")
        self.assertEqual(caught.exception.code, 401)

    def test_create_list_history_and_sse_message(self):
        created = self.request("POST", "/v1/conversations", {"title":"API test"},
            token=self.token, headers={"Idempotency-Key":"api-session-1"})
        sid = created["session_id"]
        items = self.request("GET", "/v1/conversations", token=self.token)["items"]
        self.assertTrue(any(item["session_id"] == sid for item in items))
        stream = self.request("POST", "/v1/conversations/%s/messages" % sid,
            {"content":"Say hello"}, token=self.token,
            headers={"Idempotency-Key":"api-turn-1"})
        self.assertIn('"type":"delta","content":"Hello "', stream)
        self.assertIn('"type":"completed"', stream)
        history = self.request("GET", "/v1/conversations/%s/events" % sid, token=self.token)
        self.assertEqual([e["kind"] for e in history["items"] if e["kind"].startswith("message.")],
            ["message.user", "message.assistant"])

    def test_logout_revokes_bearer_token(self):
        self.request("POST", "/v1/auth/logout", {}, token=self.token)
        with self.assertRaises(HTTPError) as caught:
            self.request("GET", "/v1/me", token=self.token)
        self.assertEqual(caught.exception.code, 401)

    def test_server_rejects_non_loopback_bind(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            serve(FakeManager(), host="0.0.0.0", port=8765, data_dir=self.temp.name)


if __name__ == "__main__":
    unittest.main()
