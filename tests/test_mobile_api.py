"""Security and contract tests for the mobile API."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from mobile.server import MobileAPIError, build_server, create_handler


class FakeProvider:
    model = "test-local-model"

    def generate(self, prompt):
        return "reply: " + prompt

    def list_models(self):
        return ["test-local-model"]


class MobileAPITests(unittest.TestCase):
    TOKEN = "test-token-that-is-at-least-32-characters-long"

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(token=self.TOKEN, provider=FakeProvider()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, *, token=None, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_health_requires_token(self):
        self.assertEqual(self.request("/api/v1/health")[0], 401)
        status, body = self.request("/api/v1/health", token=self.TOKEN)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_chat_is_text_only_and_does_not_execute_skills(self):
        status, body = self.request("/api/v1/chat", token=self.TOKEN, method="POST", body={"message": "hello"})
        self.assertEqual(status, 200)
        self.assertEqual(body["answer"], "reply: hello")
        self.assertEqual(body["execution"], "text_only_no_skills_executed")

    def test_chat_rejects_empty_or_oversized_message(self):
        self.assertEqual(self.request("/api/v1/chat", token=self.TOKEN, method="POST", body={"message": " "})[0], 400)
        self.assertEqual(self.request("/api/v1/chat", token=self.TOKEN, method="POST", body={"message": "x" * 8001})[0], 400)

    def test_unknown_routes_and_unsupported_methods_are_closed(self):
        self.assertEqual(self.request("/api/v1/execute", token=self.TOKEN)[0], 404)
        self.assertEqual(self.request("/api/v1/chat", token=self.TOKEN, method="DELETE")[0], 405)

    def test_web_client_is_served_without_exposing_api_token(self):
        request = urllib.request.Request(self.base + "/")
        with urllib.request.urlopen(request, timeout=3) as response:
            html = response.read().decode()
        self.assertIn("Yuki Mobile", html)
        self.assertNotIn(self.TOKEN, html)

    def test_token_minimum_length(self):
        with self.assertRaises(MobileAPIError):
            create_handler(token="short", provider=FakeProvider())

    def test_server_refuses_mock_or_cloud_provider(self):
        with patch("mobile.server.create_provider", return_value=FakeProvider()):
            with self.assertRaisesRegex(MobileAPIError, "Local Ollama"):
                build_server(
                    host="127.0.0.1",
                    port=8765,
                    environ={"YUKI_MOBILE_TOKEN": self.TOKEN},
                )

    def test_model_inventory_requires_authentication(self):
        self.assertEqual(self.request("/api/v1/models")[0], 401)
        status, body = self.request("/api/v1/models", token=self.TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body["installed_models"], ["test-local-model"])

    def test_remote_bind_requires_tls_certificate_configuration(self):
        with self.assertRaises(MobileAPIError):
            build_server(host="0.0.0.0", port=0, environ={"YUKI_MOBILE_TOKEN": self.TOKEN})


if __name__ == "__main__":
    unittest.main()
