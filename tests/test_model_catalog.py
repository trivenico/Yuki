import hashlib
import tempfile
import unittest
from pathlib import Path

from providers.model_catalog import (
    ModelCatalogError,
    approve_model,
    list_models,
    mark_catalog_active,
    new_catalog,
    record_evaluation,
    register_artifact,
    review_provenance,
    rollback_catalog_release,
    stage_model,
)


def manifest(digest, method="lora", version="0.1.0"):
    return {
        "schema_version": 1,
        "model_id": "yuki/core",
        "model_version": version,
        "artifact_sha256": digest,
        "source": "yuki_training_pipeline",
        "license_id": "Apache-2.0",
        "base_model": "opensource/base-model" if method != "from_scratch" else None,
        "training_method": method,
        "dataset_sha256": ["a" * 64],
        "created_at": "2026-11-05T09:00:00Z",
    }


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.artifact = Path(self.temp.name) / "model.bin"
        self.artifact.write_bytes(b"yuki model artifact")
        self.digest = hashlib.sha256(b"yuki model artifact").hexdigest()
        self.catalog = new_catalog()

    def tearDown(self):
        self.temp.cleanup()

    def register(self, method="lora", version="0.1.0"):
        return register_artifact(self.catalog, manifest(self.digest, method, version), self.artifact)

    def test_register_verifies_bytes_and_quarantines_by_default(self):
        item = self.register()
        self.assertTrue(item["artifact_verified"])
        self.assertEqual(item["state"], "quarantined")
        self.assertEqual(item["ownership"], "yuki_finetuned")
        self.assertEqual(item["provenance_status"], "unverified")
        self.assertEqual(item["license_status"], "unknown")
        self.assertEqual(item["activation_policy"], "manual_review_required")

    def test_distinguishes_imported_and_from_scratch(self):
        self.assertEqual(self.register("imported")["ownership"], "imported")
        self.assertEqual(self.register("from_scratch", "0.2.0")["ownership"], "yuki_trained_from_scratch")

    def test_rejects_digest_mismatch_and_duplicate(self):
        bad = manifest("0" * 64)
        with self.assertRaisesRegex(ModelCatalogError, "artifact_digest_mismatch"):
            register_artifact(self.catalog, bad, self.artifact)
        self.register()
        with self.assertRaisesRegex(ModelCatalogError, "already_registered"):
            self.register()

    def test_rejects_evaluation_before_provenance_review(self):
        self.register()
        with self.assertRaisesRegex(ModelCatalogError, "evaluation_requires_reviewed"):
            record_evaluation(self.catalog, "yuki/core", "0.1.0", evaluation_id="eval-1", passed=True)

    def test_restricted_license_cannot_advance(self):
        self.register()
        item = review_provenance(self.catalog, "yuki/core", "0.1.0",
                                 provenance_status="verified", license_status="restricted", reviewer="reviewer-1")
        self.assertEqual(item["state"], "quarantined")

    def test_full_lifecycle_requires_each_gate_and_never_claims_runtime_activation(self):
        self.register()
        review_provenance(self.catalog, "yuki/core", "0.1.0",
                          provenance_status="verified", license_status="verified", reviewer="reviewer-1")
        record_evaluation(self.catalog, "yuki/core", "0.1.0", evaluation_id="eval-1", passed=True)
        approve_model(self.catalog, "yuki/core", "0.1.0", approver="owner-1")
        with self.assertRaisesRegex(ModelCatalogError, "runtime_compatibility"):
            stage_model(self.catalog, "yuki/core", "0.1.0", runtime_status="unknown")
        stage_model(self.catalog, "yuki/core", "0.1.0", runtime_status="verified")
        active = mark_catalog_active(self.catalog, "yuki/core", "0.1.0", approver="release-owner")
        self.assertEqual(active["state"], "active")
        self.assertEqual(active["activation_policy"], "manual_review_required")
        rolled = rollback_catalog_release(self.catalog, "yuki/core", "0.1.0", approver="release-owner")
        self.assertEqual(rolled["state"], "rolled_back")
        self.assertEqual(len(list_models(self.catalog)), 1)

    def test_failed_evaluation_cannot_be_approved(self):
        self.register()
        review_provenance(self.catalog, "yuki/core", "0.1.0",
                          provenance_status="verified", license_status="verified", reviewer="reviewer-1")
        record_evaluation(self.catalog, "yuki/core", "0.1.0", evaluation_id="eval-1", passed=False)
        with self.assertRaisesRegex(ModelCatalogError, "passing_evaluation"):
            approve_model(self.catalog, "yuki/core", "0.1.0", approver="owner-1")


if __name__ == "__main__":
    unittest.main()
