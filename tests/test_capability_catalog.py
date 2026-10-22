import unittest

from core.capability_catalog import capability_catalog, format_capability_catalog


class CapabilityCatalogTests(unittest.TestCase):
    def test_catalog_has_unique_ids_and_required_fields(self):
        items = capability_catalog()
        self.assertTrue(items)
        self.assertEqual(len({item["id"] for item in items}), len(items))
        for item in items:
            self.assertTrue(item["label"])
            self.assertTrue(item["status"])
            self.assertTrue(item["description"])
            self.assertTrue(item["permission"])

    def test_high_risk_features_are_not_advertised_as_fully_available(self):
        items = {item["id"]: item for item in capability_catalog()}
        self.assertEqual(items["screen-understanding"]["status"], "unavailable")
        self.assertEqual(items["automatic-production-deploy"]["status"], "blocked")
        self.assertEqual(items["self-improvement"]["status"], "supervised")

    def test_catalog_is_defensive_and_cli_report_is_human_readable(self):
        items = capability_catalog()
        items[0]["status"] = "fully available"
        self.assertNotEqual(capability_catalog()[0]["status"], "fully available")
        report = format_capability_catalog()
        self.assertIn("Permission boundary", report)
        self.assertIn("screen understanding", report.lower())


if __name__ == "__main__":
    unittest.main()
