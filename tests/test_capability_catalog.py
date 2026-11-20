import unittest

from automation.capability_catalog import (
    CAPABILITIES, catalog_summary, get_capability, search_capabilities,
)


class CapabilityCatalogTests(unittest.TestCase):
    def test_catalog_has_one_hundred_unique_stable_ids(self):
        self.assertEqual(len(CAPABILITIES), 100)
        ids = [item.capability_id for item in CAPABILITIES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len({item.name for item in CAPABILITIES}), 100)

    def test_entries_are_planned_and_non_executable_by_default(self):
        self.assertTrue(all(item.status == "planned" for item in CAPABILITIES))
        self.assertTrue(all(item.execution_mode == "not_implemented" for item in CAPABILITIES))
        self.assertTrue(all(item.approval_required for item in CAPABILITIES))
        self.assertTrue(all(item.permissions == () for item in CAPABILITIES))

    def test_lookup_is_case_insensitive_and_does_not_activate(self):
        item = get_capability("CODE-REVIEWER")
        self.assertEqual(item.name, "Code-Reviewer")
        self.assertEqual(item.status, "planned")
        self.assertIsNone(get_capability("../code-reviewer"))
        self.assertIsNone(get_capability(None))

    def test_search_is_deterministic_and_bounded(self):
        first = search_capabilities("review code security", limit=5)
        second = search_capabilities("review code security", limit=5)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 5)
        self.assertIn("code-reviewer", {item.capability_id for item in first})

    def test_search_handles_empty_queries_and_validates_limit(self):
        self.assertEqual(search_capabilities("   "), ())
        with self.assertRaises(ValueError):
            search_capabilities("research", limit=0)
        with self.assertRaises(ValueError):
            search_capabilities("research", limit=True)

    def test_summary_reports_ten_domains_and_planned_state(self):
        summary = catalog_summary()
        self.assertEqual(summary["total"], 100)
        self.assertEqual(len(summary["domains"]), 10)
        self.assertEqual(summary["statuses"], {"planned": 100})


if __name__ == "__main__":
    unittest.main()
