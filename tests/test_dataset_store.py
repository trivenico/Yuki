import tempfile
import unittest
from pathlib import Path

from providers.dataset_store import DatasetStore, DatasetStoreError


def dataset_manifest():
    return {
        "schema_version": 1,
        "dataset_id": "yuki/core-instruct",
        "dataset_revision": "2026-11-05.1",
        "source_uri": "https://example.org/datasets/core",
        "source_revision": "git:0123456789abcdef",
        "artifact_sha256": "a" * 64,
        "license_id": "Apache-2.0",
        "license_evidence": "review-ticket:DATA-001",
        "rights_status": "unverified",
        "consent_status": "unverified",
        "privacy_status": "unverified",
        "review_status": "quarantined",
        "size_bytes": 1024,
        "file_count": 4,
        "retrieved_at": "2026-11-05T09:00:00Z",
        "contains_executable_code": False,
    }


class DatasetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "datasets.sqlite3"
        self.store = DatasetStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_registers_new_dataset_in_quarantine_and_reads_it_back(self):
        result = self.store.register(dataset_manifest(), actor="reviewer-1")
        self.assertEqual(result["state"], "quarantined")
        stored = self.store.get("yuki/core-instruct", "2026-11-05.1")
        self.assertEqual(stored["state"], "quarantined")
        self.assertEqual(stored["manifest"]["artifact_sha256"], "a" * 64)
        self.assertTrue(self.store.verify_audit_chain())

    def test_duplicate_revision_is_rejected(self):
        self.store.register(dataset_manifest(), actor="reviewer-1")
        with self.assertRaisesRegex(DatasetStoreError, "dataset_revision_already_exists"):
            self.store.register(dataset_manifest(), actor="reviewer-1")

    def test_approved_manifest_can_be_registered_but_requires_all_reviews(self):
        item = dataset_manifest()
        item.update({
            "rights_status": "verified",
            "consent_status": "verified",
            "privacy_status": "verified",
            "review_status": "approved",
        })
        result = self.store.register(item, actor="reviewer-2")
        self.assertEqual(result["state"], "approved")

    def test_rejects_invalid_actor_and_list_limit(self):
        with self.assertRaisesRegex(DatasetStoreError, "invalid_audit_actor"):
            self.store.register(dataset_manifest(), actor="../bad")
        with self.assertRaisesRegex(ValueError, "invalid_dataset_limit"):
            self.store.list_datasets(limit=True)

    def test_reopen_preserves_records_and_audit(self):
        self.store.register(dataset_manifest(), actor="reviewer-1")
        reopened = DatasetStore(self.path)
        self.assertEqual(len(reopened.list_datasets()), 1)
        self.assertTrue(reopened.verify_audit_chain())

    def test_rejects_symlink_database_path(self):
        link = Path(self.temp.name) / "dataset-link.sqlite3"
        try:
            link.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(DatasetStoreError, "symlink"):
            DatasetStore(link)


if __name__ == "__main__":
    unittest.main()
