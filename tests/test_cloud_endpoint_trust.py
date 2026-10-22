import unittest
from providers.openai_compatible import OpenAICompatibleProvider, _trusted_https_endpoint

class CloudEndpointTrustTests(unittest.TestCase):
    def test_known_provider_hosts_are_accepted(self):
        self.assertEqual(_trusted_https_endpoint("https://api.openai.com/v1"), "https://api.openai.com/v1")
        self.assertEqual(_trusted_https_endpoint("https://openrouter.ai/api/v1"), "https://openrouter.ai/api/v1")
        self.assertEqual(_trusted_https_endpoint("https://api.deepseek.com"), "https://api.deepseek.com")

    def test_custom_host_requires_explicit_allowlist(self):
        with self.assertRaisesRegex(ValueError, "not trusted"):
            OpenAICompatibleProvider("model-x", base_url="https://gateway.example.test/v1")
        provider = OpenAICompatibleProvider("model-x", base_url="https://gateway.example.test/v1", allowed_hosts={"gateway.example.test"})
        self.assertEqual(provider.base_url, "https://gateway.example.test/v1")

    def test_rejects_http_credentials_query_fragment_and_private_ip(self):
        bad_urls = [
            "http://api.openai.com/v1",
            "https://user:password@api.openai.com/v1",
            "https://api.openai.com/v1?token=secret",
            "https://api.openai.com/v1#fragment",
            "https://127.0.0.1/v1",
            "https://10.0.0.1/v1",
            "https://192.168.1.5/v1",
        ]
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _trusted_https_endpoint(url, allowed_hosts={"127.0.0.1", "10.0.0.1", "192.168.1.5"})

    def test_rejects_invalid_timeouts_and_key_env_names(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider("model-x", timeout=0)
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider("model-x", api_key_env="KEY-NAME")

    def test_host_matching_is_exact(self):
        with self.assertRaisesRegex(ValueError, "not trusted"):
            _trusted_https_endpoint("https://api.openai.com.attacker.test/v1")

if __name__ == "__main__":
    unittest.main()
