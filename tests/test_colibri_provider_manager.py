import unittest

from providers.colibri import ColibriProvider
from yuki_providers import ProviderManager


class ColibriProviderManagerTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "YUKI_PROVIDER": "colibri",
            "YUKI_COLIBRI_MODEL": "tiny-local-model",
            "YUKI_COLIBRI_BASE_URL": "http://127.0.0.1:8000/v1",
        }

    def test_colibri_is_listed_as_implemented(self):
        manager = ProviderManager(environ=self.env)
        providers = {item["name"]: item for item in manager.list_providers()}
        self.assertTrue(providers["colibri"]["implemented"])
        self.assertTrue(providers["colibri"]["selected"])

    def test_colibri_can_be_selected_through_provider_manager(self):
        manager = ProviderManager(environ=self.env)
        self.assertEqual(manager.select("colibri"), "Selected provider: colibri")
        self.assertIsInstance(manager.create_selected_adapter(), ColibriProvider)

    def test_catalog_uses_environment_selected_provider(self):
        manager = ProviderManager(environ=self.env)
        catalog = {item["name"]: item for item in manager.catalog()}
        self.assertTrue(catalog["colibri"]["selected"])
        self.assertEqual(catalog["colibri"]["configured_model"], "tiny-local-model")

    def test_manager_does_not_probe_runtime_when_selecting(self):
        manager = ProviderManager(environ=self.env)
        self.assertEqual(manager.select("colibri"), "Selected provider: colibri")
        # Construction/selection is offline; only explicit availability checks probe the server.
        self.assertIsInstance(manager.create_selected_adapter(), ColibriProvider)


if __name__ == "__main__":
    unittest.main()
