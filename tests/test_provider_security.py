"""Provider endpoint and redirect security regression tests.

These tests use only loopback sockets and never contact a public service.
"""
from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from providers.http_client import open_provider_request
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


class _RedirectHandler(BaseHTTPRequestHandler):
    destination_hits = 0

    def do_GET(self):
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/destination")
            self.end_headers()
            return
        if self.path == "/destination":
            type(self).destination_hits += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"should not be reached")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Keep test output deterministic and free of request details.
        return


class ProviderEndpointSecurityTests(unittest.TestCase):
    def test_ollama_rejects_plain_http_non_loopback_endpoint(self):
        with self.assertRaises(ValueError):
            OllamaProvider(base_url="http://model.internal:11434", model="test")

    def test_ollama_rejects_url_credentials(self):
        with self.assertRaises(ValueError):
            OllamaProvider(
                base_url="https://user:password@model.example.test",
                model="test",
            )

    def test_openai_compatible_requires_https(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider(
                "test-model",
                base_url="http://api.example.test/v1",
            )

    def test_provider_http_client_does_not_follow_redirects(self):
        _RedirectHandler.destination_hits = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            import urllib.error
            import urllib.request

            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/start"
            )
            with self.assertRaises(urllib.error.HTTPError):
                open_provider_request(request, timeout=2)
            self.assertEqual(_RedirectHandler.destination_hits, 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
