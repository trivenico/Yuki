import unittest

from core.approval_preview import redact_preview


class ApprovalPreviewRedactionTests(unittest.TestCase):
    def test_redacts_sensitive_values_recursively(self):
        preview = redact_preview({
            "skill_id": "notes.write",
            "arguments": {
                "api_token": "secret-token",
                "nested": {"password": "secret-password", "message": "hello"},
            },
        })
        self.assertEqual(preview["arguments"]["api_token"], "[REDACTED]")
        self.assertEqual(preview["arguments"]["nested"]["password"], "[REDACTED]")
        self.assertEqual(preview["arguments"]["nested"]["message"], "hello")

    def test_bounds_depth_collection_size_and_string_length(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "value"}}}}}}}
        preview = redact_preview({
            "deep": deep,
            "items": list(range(75)),
            "large": "x" * 1500,
        })
        self.assertEqual(preview["deep"]["a"]["b"]["c"]["d"]["e"], "[truncated]")
        self.assertEqual(len(preview["items"]), 51)
        self.assertEqual(preview["items"][-1], "[truncated]")
        self.assertTrue(preview["large"].endswith("…[truncated]"))
        self.assertLessEqual(len(preview["large"]), 1012)

    def test_does_not_echo_unsupported_objects(self):
        class Secret:
            def __str__(self):
                return "must-not-leak"
        self.assertEqual(redact_preview({"value": Secret()}), {"value": "[unsupported]"})


if __name__ == "__main__":
    unittest.main()
