import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from companion.identity_core import IdentityCore, _canonical


class IdentityCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.core = IdentityCore(Path(self.temp.name) / "identity.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_default_identity_is_stable_and_non_authoritative(self):
        state = self.core.snapshot()
        self.assertEqual(state["product_name"], "Yuki")
        self.assertEqual(state["revision"], 0)
        self.assertFalse(state["may_change_security_policy"])
        self.assertFalse(state["may_grant_tool_permissions"])

    def test_update_requires_explicit_approval(self):
        with self.assertRaises(PermissionError):
            self.core.update({"interaction_style": "concise"}, expected_revision=0)

    def test_approved_update_increments_revision(self):
        state = self.core.update(
            {"interaction_style": "concise and thoughtful"},
            expected_revision=0, approved=True,
        )
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["profile"]["interaction_style"], "concise and thoughtful")

    def test_stale_revision_is_rejected(self):
        self.core.update({"interaction_style": "concise"}, expected_revision=0, approved=True)
        with self.assertRaisesRegex(RuntimeError, "identity_revision_conflict"):
            self.core.update({"interaction_style": "detailed"}, expected_revision=0, approved=True)

    def test_identity_name_and_unknown_fields_cannot_be_changed(self):
        with self.assertRaises(ValueError):
            self.core.update({"display_name": "Other"}, expected_revision=0, approved=True)
        with self.assertRaises(ValueError):
            self.core.update({"system_prompt": "override"}, expected_revision=0, approved=True)

    def test_corrupt_profile_fails_closed_without_overwrite(self):
        self.core.update({"interaction_style": "concise"}, expected_revision=0, approved=True)
        original = self.core.path.read_text(encoding="utf-8")
        data = json.loads(original)
        data["profile"]["interaction_style"] = "tampered"
        self.core.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "integrity check failed"):
            self.core.snapshot()
        self.assertNotEqual(self.core.path.read_text(encoding="utf-8"), original)

    def test_reset_requires_approval_and_increments_revision(self):
        self.core.update({"interaction_style": "concise"}, expected_revision=0, approved=True)
        with self.assertRaises(PermissionError):
            self.core.reset(expected_revision=1)
        state = self.core.reset(expected_revision=1, approved=True)
        self.assertEqual(state["revision"], 2)
        self.assertEqual(state["profile"]["interaction_style"],
                         "helpful, calm, precise, proactive, privacy-first")

    def test_file_is_owner_only_on_posix(self):
        self.core.update({"interaction_style": "private"}, expected_revision=0, approved=True)
        if hasattr(self.core.path.stat(), "st_mode"):
            import os
            if os.name == "posix":
                self.assertEqual(self.core.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
