import unittest
from providers.factory import create_provider
from providers.local_openai_compatible import LocalOpenAICompatibleProvider
from providers.catalog import build_provider_catalog


class OpenSourceRuntimeFactoryTests(unittest.TestCase):
    def test_factory_builds_loopback_runtime_without_network_access(self):
        provider = create_provider(
            {"provider": "local_openai_compatible"},
            environ={"YUKI_LOCAL_OPENAI_MODEL": "served-model"},
        )
        self.assertIsInstance(provider, LocalOpenAICompatibleProvider)
        self.assertEqual(provider.base_url, "http://127.0.0.1:8000/v1")

    def test_factory_requires_explicit_served_model(self):
        with self.assertRaisesRegex(ValueError, "YUKI_LOCAL_OPENAI_MODEL"):
            create_provider({"provider": "local_openai_compatible"}, environ={})

    def test_factory_rejects_non_loopback_endpoint(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            create_provider(
                {"provider": "local_openai_compatible"},
                environ={"YUKI_LOCAL_OPENAI_MODEL": "served-model",
                         "YUKI_LOCAL_OPENAI_BASE_URL": "https://example.com/v1"},
            )

    def test_catalog_reports_configured_runtime_without_claiming_live_probe(self):
        catalog = build_provider_catalog(
            config={"provider": "local_openai_compatible"},
            environ={"YUKI_LOCAL_OPENAI_MODEL": "served-model"},
        )
        item = next(x for x in catalog if x["name"] == "local_openai_compatible")
        self.assertTrue(item["ready"])
        self.assertEqual(item["status"], "local_endpoint_configured_runtime_not_probed")
        self.assertNotIn("api_key", item)


if __name__ == "__main__":
    unittest.main()
