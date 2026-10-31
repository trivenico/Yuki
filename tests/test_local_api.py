import io
import json
import unittest

from core.local_api import LocalAPI


class FakeRuntime:
    def __init__(self):
        self.active_conversation_id = "conversation_1"
        self.messages = []
        self.calls = []

    def history(self):
        return list(self.messages)

    def send_message(self, message):
        self.calls.append(message)
        self.messages.extend([
            {"role": "user", "content": message},
            {"role": "assistant", "content": "reply:" + message},
        ])
        return "reply:" + message

    def new_conversation(self, title):
        self.active_conversation_id = "conversation_2"
        self.messages = []
        return self.active_conversation_id


def request(app, method="GET", path="/v1/health", payload=None, token="x" * 40,
            remote="127.0.0.1", origin=None):
    raw = b"" if payload is None else json.dumps(payload).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)) if raw else "",
        "wsgi.input": io.BytesIO(raw),
        "REMOTE_ADDR": remote,
        "HTTP_AUTHORIZATION": "Bearer " + token,
    }
    if origin:
        environ["HTTP_ORIGIN"] = origin
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    captured["body"] = json.loads(body.decode())
    return captured


class LocalAPITests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.token = "t" * 48
        self.app = LocalAPI(self.runtime, self.token, allowed_origins=("http://127.0.0.1:3000",))

    def test_health_requires_loopback_and_bearer_token(self):
        self.assertEqual(request(self.app, token=self.token)["status"], "200 OK")
        self.assertEqual(request(self.app, token="wrong")["status"], "401 Unauthorized")
        self.assertEqual(request(self.app, token=self.token, remote="192.0.2.5")["status"], "403 Forbidden")

    def test_origin_is_exact_and_no_cors_preflight_is_granted(self):
        self.assertEqual(
            request(self.app, token=self.token, origin="http://127.0.0.1:3000")["status"],
            "200 OK",
        )
        self.assertEqual(
            request(self.app, token=self.token, origin="http://evil.example")["status"],
            "403 Forbidden",
        )
        self.assertEqual(
            request(self.app, method="OPTIONS", token=self.token)["status"],
            "405 Method Not Allowed",
        )

    def test_chat_persists_via_shared_runtime_and_history_is_readable(self):
        result = request(self.app, "POST", "/v1/chat", {"message": "hello"}, self.token)
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["body"]["response"], "reply:hello")
        history = request(self.app, token=self.token, path="/v1/history")
        self.assertEqual(len(history["body"]["messages"]), 2)
        self.assertEqual(self.runtime.calls, ["hello"])

    def test_chat_rejects_extra_fields_and_non_string_message(self):
        self.assertEqual(
            request(self.app, "POST", "/v1/chat", {"message": "hello", "tool": "shell"}, self.token)["status"],
            "400 Bad Request",
        )
        self.assertEqual(
            request(self.app, "POST", "/v1/chat", {"message": []}, self.token)["status"],
            "400 Bad Request",
        )

    def test_conversation_creation_uses_runtime(self):
        result = request(self.app, "POST", "/v1/conversations", {"title": "New"}, self.token)
        self.assertEqual(result["status"], "201 Created")
        self.assertEqual(result["body"]["conversation_id"], "conversation_2")

    def test_rejects_weak_token_and_non_origin_url(self):
        with self.assertRaises(ValueError):
            LocalAPI(self.runtime, "short")
        with self.assertRaises(ValueError):
            LocalAPI(self.runtime, self.token, allowed_origins=("https://example.com/path",))


if __name__ == "__main__":
    unittest.main()
