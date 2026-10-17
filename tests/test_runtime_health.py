import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from core.runtime_health import _ollama_ready, build_runtime_health_manager, summarize_health


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"models": [{"name": "test-model:latest"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class RuntimeHealthTests(unittest.TestCase):
    def test_workspace_and_safe_plugin_defaults(self):
        with TemporaryDirectory() as tmp:
            config = {"provider": "mock", "plugin_discovery_enabled": False,
                      "trusted_plugin_modules": [], "voice_enabled": False}
            rows = {name: (status, message) for name, status, message
                    in summarize_health(build_runtime_health_manager(config, Path(tmp)))}
            self.assertEqual(rows["workspace"][0], "healthy")
            self.assertEqual(rows["plugin_trust_policy"][0], "healthy")
            self.assertEqual(rows["provider_configuration"][0], "healthy")

    def test_plugin_wildcard_and_empty_enabled_allowlist_fail(self):
        for trusted in (["*"], []):
            with self.subTest(trusted=trusted), TemporaryDirectory() as tmp:
                config = {"provider": "mock", "plugin_discovery_enabled": True,
                          "trusted_plugin_modules": trusted, "voice_enabled": False}
                rows = {name: status for name, status, _ in
                        summarize_health(build_runtime_health_manager(config, Path(tmp)))}
                self.assertEqual(rows["plugin_trust_policy"], "unhealthy")

    def test_missing_piper_model_is_reported(self):
        with TemporaryDirectory() as tmp:
            config = {"provider": "mock", "plugin_discovery_enabled": False,
                      "trusted_plugin_modules": [], "voice_enabled": True,
                      "tts_backend": "piper", "piper_model_path": str(Path(tmp) / "missing.onnx")}
            rows = {name: status for name, status, _ in
                    summarize_health(build_runtime_health_manager(config, Path(tmp)))}
            self.assertEqual(rows["piper_voice_model"], "unhealthy")

    def test_ollama_check_is_local_only(self):
        self.assertFalse(_ollama_ready("https://example.com", "test-model"))
        self.assertFalse(_ollama_ready("http://192.0.2.10:11434", "test-model"))

    def test_ollama_model_inventory_uses_bounded_local_request(self):
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertTrue(_ollama_ready(f"http://127.0.0.1:{server.server_port}", "test-model:latest"))


if __name__ == "__main__":
    unittest.main()
