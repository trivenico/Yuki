import unittest

from core.context_budget import context_budget


class ContextBudgetTests(unittest.TestCase):
    def test_known_context_budget_is_calculated(self):
        result = context_budget(limit_tokens=8192, used_tokens=3072)
        self.assertTrue(result["available"])
        self.assertEqual(result["remaining_tokens"], 5120)
        self.assertFalse(result["estimated"])

    def test_estimated_usage_is_explicitly_labelled(self):
        result = context_budget(limit_tokens=4096, used_tokens=1024, estimated=True)
        self.assertEqual(result["remaining_tokens"], 3072)
        self.assertTrue(result["estimated"])
        self.assertIn("estimated", result["detail"].lower())

    def test_missing_or_invalid_metadata_remains_unknown(self):
        for limit, used in ((None, 10), (8192, None), (0, 0), (-1, 0),
                            (8192, -1), (8192, 8193), (True, 1), (8192, True)):
            with self.subTest(limit=limit, used=used):
                result = context_budget(limit_tokens=limit, used_tokens=used)
                self.assertFalse(result["available"])
                self.assertIsNone(result["remaining_tokens"])
                self.assertIsNone(result["limit_tokens"])

    def test_estimated_flag_must_be_boolean(self):
        with self.assertRaises(ValueError):
            context_budget(limit_tokens=10, used_tokens=5, estimated="false")


if __name__ == "__main__":
    unittest.main()
