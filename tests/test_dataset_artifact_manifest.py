import unittest

from providers.dataset_artifact_manifest import (
    DatasetManifestError,
    assert_training_eligible,
    validate_dataset_manifest,
)


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


class DatasetArtifactManifestTests(unittest.TestCase):
    def test_new_dataset_is_quarantined_by_default(self):
        normalized = validate_dataset_manifest(dataset_manifest())
        self.assertFalse(normalized["training_eligible"])
        self.assertTrue(normalized["quarantine_required"])

    def test_training_requires_all_independent_reviews(self):
        item = dataset_manifest()
        item.update({
            "rights_status": "verified",
            "consent_status": "verified",
            "privacy_status": "verified",
            "review_status": "approved",
        })
        normalized = assert_training_eligible(item)
        self.assertTrue(normalized["training_eligible"])
        self.assertFalse(normalized["quarantine_required"])

    def test_unverified_rights_block_training(self):
        item = dataset_manifest()
        item.update({
            "consent_status": "verified",
            "privacy_status": "verified",
            "review_status": "approved",
        })
        with self.assertRaisesRegex(DatasetManifestError, "dataset_not_approved_for_training"):
            assert_training_eligible(item)

    def test_executable_dataset_is_never_training_eligible(self):
        item = dataset_manifest()
        item.update({
            "rights_status": "verified",
            "consent_status": "verified",
            "privacy_status": "verified",
            "review_status": "approved",
            "contains_executable_code": True,
        })
        with self.assertRaisesRegex(DatasetManifestError, "dataset_not_approved_for_training"):
            assert_training_eligible(item)

    def test_rejects_unknown_fields_and_invalid_hash(self):
        for change in ({"unexpected": "field"}, {"artifact_sha256": "not-a-hash"}):
            item = dataset_manifest()
            item.update(change)
            with self.subTest(change=change):
                with self.assertRaises(DatasetManifestError):
                    validate_dataset_manifest(item)

    def test_rejects_boolean_sizes_and_counts(self):
        for field in ("size_bytes", "file_count"):
            item = dataset_manifest()
            item[field] = True
            with self.subTest(field=field):
                with self.assertRaises(DatasetManifestError):
                    validate_dataset_manifest(item)

    def test_rejects_unknown_review_states(self):
        item = dataset_manifest()
        item["rights_status"] = "probably_ok"
        with self.assertRaisesRegex(DatasetManifestError, "invalid_rights_status"):
            validate_dataset_manifest(item)


if __name__ == "__main__":
    unittest.main()
