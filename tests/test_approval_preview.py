import unittest

from core.approval_preview import confirmation_digest, redact_preview, verify_confirmation_digest


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

    def test_confirmation_digest_binds_exact_snapshot_and_is_stable(self):
        first = {"skill_id": "notes.write", "arguments": {"text": "hello", "count": 1}}
        reordered = {"arguments": {"count": 1, "text": "hello"}, "skill_id": "notes.write"}
        changed = {"skill_id": "notes.write", "arguments": {"text": "changed", "count": 1}}
        digest = confirmation_digest(first)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, confirmation_digest(reordered))
        self.assertNotEqual(digest, confirmation_digest(changed))

    def test_confirmation_verification_requires_exact_valid_digest(self):
        action = {"skill_id": "notes.write", "arguments": {"text": "approve this exact note"}}
        digest = confirmation_digest(action)
        self.assertTrue(verify_confirmation_digest(action, digest))
        self.assertFalse(verify_confirmation_digest(
            {"skill_id": "notes.write", "arguments": {"text": "different note"}}, digest
        ))
        self.assertFalse(verify_confirmation_digest(action, digest.upper()))
        self.assertFalse(verify_confirmation_digest(action, "not-a-digest"))
        self.assertFalse(verify_confirmation_digest(action, None))

    def test_confirmation_digest_rejects_non_json_values(self):
        with self.assertRaises(ValueError):
            confirmation_digest({"value": float("nan")})

    def test_does_not_echo_unsupported_objects(self):
        class Secret:
            def __str__(self):
                return "must-not-leak"
        self.assertEqual(redact_preview({"value": Secret()}), {"value": "[unsupported]"})


if __name__ == "__main__":
    unittest.main()
