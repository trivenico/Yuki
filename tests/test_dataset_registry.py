import unittest

from learning.dataset_registry import DatasetApproval, DatasetRegistry


class DatasetRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = DatasetRegistry()
        self.approval = DatasetApproval(
            repo_id="openai/gsm8k",
            revision="a" * 40,
            license_id="MIT",
            license_evidence_url="https://huggingface.co/datasets/openai/gsm8k",
            provenance_reviewed=True,
            privacy_reviewed=True,
            security_reviewed=True,
            human_approved=True,
            approved_by="reviewer",
        )

    def test_candidates_are_not_auto_downloaded_or_training_eligible(self):
        items = self.registry.candidates()
        self.assertGreaterEqual(len(items), 4)
        self.assertTrue(all(not item["automatic_download"] for item in items))
        self.assertTrue(all(not item["training_eligible"] for item in items))

    def test_requires_pinned_revision_and_all_reviews(self):
        from dataclasses import replace
        with self.assertRaises(ValueError):
            self.registry.approve(replace(self.approval, revision="main"))
        with self.assertRaises(PermissionError):
            self.registry.approve(replace(self.approval, privacy_reviewed=False))

    def test_rejects_unapproved_or_unknown_license(self):
        from dataclasses import replace
        with self.assertRaises(PermissionError):
            self.registry.approve(replace(self.approval, license_id="unknown"))
        with self.assertRaises(ValueError):
            self.registry.approve(replace(self.approval, repo_id="someone/unknown"))

    def test_approval_is_pinned_and_explicit(self):
        result = self.registry.approve(self.approval)
        self.assertTrue(result["training_eligible"])
        self.assertFalse(result["automatic_download"])
        self.assertTrue(self.registry.is_approved("openai/gsm8k", "a" * 40))
        self.assertFalse(self.registry.is_approved("openai/gsm8k", "b" * 40))

    def test_signed_approval_persists_across_registry_restart(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approvals.sqlite"
            key = b"test-only-signing-key-at-least-32-bytes"
            first = DatasetRegistry(database_path=path, signing_key=key)
            first.approve(self.approval)
            reopened = DatasetRegistry(database_path=path, signing_key=key)
            self.assertTrue(reopened.is_approved("openai/gsm8k", "a" * 40))
            self.assertEqual(reopened.approved_records()[0]["approved_by"], "reviewer")

    def test_persistent_registry_rejects_missing_or_short_signing_key(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                DatasetRegistry(database_path=Path(tmp) / "approvals.sqlite", signing_key=b"short")

    def test_persistent_registry_fails_closed_on_tampered_approval(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approvals.sqlite"
            key = b"test-only-signing-key-at-least-32-bytes"
            first = DatasetRegistry(database_path=path, signing_key=key)
            first.approve(self.approval)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE dataset_approvals SET approval_json=?",
                           ('{"repo_id":"someone/else"}',))
            with self.assertRaises(RuntimeError):
                DatasetRegistry(database_path=path, signing_key=key)


if __name__ == "__main__":
    unittest.main()
