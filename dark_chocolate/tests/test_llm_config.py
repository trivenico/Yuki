import unittest
from dark_chocolate.llm import OllamaClient

class OllamaEndpointTests(unittest.TestCase):
    def test_accepts_loopback_endpoint(self):
        client = OllamaClient("http://127.0.0.1:11434")
        self.assertEqual(client.base_url, "http://127.0.0.1:11434")
    def test_rejects_lookalike_hostname_and_remote_http(self):
        for endpoint in ("http://127.0.0.1.attacker.invalid", "http://example.com", "https://example.com", "http://user:pass@localhost:11434"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    OllamaClient(endpoint)

if __name__ == "__main__":
    unittest.main()
