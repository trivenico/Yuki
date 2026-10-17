"""Regression tests for plugin discovery trust policy."""
import unittest

from core.plugin_loader import PluginLoader


class PluginLoaderTrustTests(unittest.TestCase):
    def test_wildcard_is_not_a_trust_grant(self):
        loader = PluginLoader(enabled=True, trusted_modules={"*"})
        report = loader.discover()
        self.assertEqual(report["loaded"], [])
        self.assertTrue(report["skipped"])
        self.assertTrue(all(
            "explicit per-module trust list" in item["reason"]
            for item in report["skipped"]
        ))

    def test_discovery_is_disabled_by_default(self):
        report = PluginLoader().discover()
        self.assertEqual(report["loaded"], [])
        self.assertEqual(report["skipped"][0]["reason"], "plugin discovery disabled")

    def test_missing_explicit_allowlist_does_not_load_modules(self):
        report = PluginLoader(enabled=True, trusted_modules=None).discover()
        self.assertEqual(report["loaded"], [])
        self.assertTrue(report["skipped"])


if __name__ == "__main__":
    unittest.main()
