"""Phase 37 regression tests for fail-closed plugin loader configuration."""
from __future__ import annotations

import unittest

from core.plugin_loader import PluginLoader


class PluginLoaderPolicyTests(unittest.TestCase):
    def test_plugin_discovery_defaults_to_disabled(self):
        loader = PluginLoader()
        report = loader.discover()
        self.assertEqual(report["loaded"], [])
        self.assertEqual(report["skipped"][0]["reason"], "plugin discovery disabled")

    def test_enabled_without_explicit_trust_list_loads_nothing(self):
        loader = PluginLoader(enabled=True)
        report = loader.discover()
        self.assertEqual(report["loaded"], [])
        self.assertEqual(report["skipped"][0]["reason"], "no exact trusted modules configured")

    def test_wildcard_trust_is_rejected(self):
        with self.assertRaises(ValueError):
            PluginLoader(enabled=True, trusted_modules=["*"])

    def test_truthy_non_boolean_enabled_value_is_rejected(self):
        with self.assertRaises(TypeError):
            PluginLoader(enabled="true")

    def test_string_is_not_accepted_as_a_trust_list(self):
        with self.assertRaises(TypeError):
            PluginLoader(enabled=True, trusted_modules="browser")

    def test_invalid_or_dotted_module_names_are_rejected(self):
        for value in ("../evil", "skills.browser", "", "browser.py"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PluginLoader(enabled=True, trusted_modules=[value])

    def test_duplicate_trust_entries_are_rejected(self):
        with self.assertRaises(ValueError):
            PluginLoader(enabled=True, trusted_modules=["browser", "browser"])


if __name__ == "__main__":
    unittest.main()
