import unittest

from providers.model_catalog import new_catalog
from providers.ollama_model_inventory import InstalledModel
from providers.ollama_catalog_reconciliation import reconcile_ollama_inventory


class OllamaCatalogReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.catalog = {
            "schema_version": 1,
            "models": [{
                "model_id": "yuki/core", "model_version": "0.1.0",
                "artifact_sha256": self.digest, "ownership_status": "unverified",
                "state": "quarantined",
            }],
        }

    def test_digest_match_does_not_approve_or_activate(self):
        installed = [InstalledModel("yuki-core:latest", "sha256:" + self.digest,
                                    100, None, None, None)]
        before = repr(self.catalog)
        report = reconcile_ollama_inventory(installed, self.catalog)
        self.assertEqual(report["installed_models"][0]["status"], "digest_match")
        self.assertEqual(report["installed_models"][0]["catalog_state"], "quarantined")
        self.assertFalse(report["automatic_activation"])
        self.assertFalse(report["catalog_mutated"])
        self.assertEqual(repr(self.catalog), before)

    def test_name_only_or_missing_digest_is_not_a_match(self):
        installed = [InstalledModel("yuki/core", None, 100, None, None, None)]
        report = reconcile_ollama_inventory(installed, self.catalog)
        self.assertEqual(report["installed_models"][0]["status"], "unmatched")
        self.assertIsNone(report["installed_models"][0]["matched_artifact"])
        self.assertFalse(report["installed_models"][0]["name_is_identity_proof"])

    def test_ambiguous_digest_is_not_resolved_arbitrarily(self):
        duplicate = dict(self.catalog["models"][0], model_version="0.2.0")
        self.catalog["models"].append(duplicate)
        installed = [InstalledModel("yuki-core:latest", self.digest, 100, None, None, None)]
        report = reconcile_ollama_inventory(installed, self.catalog)
        self.assertEqual(report["installed_models"][0]["status"], "ambiguous_digest_match")
        self.assertIsNone(report["installed_models"][0]["matched_artifact"])

    def test_invalid_inventory_record_and_duplicate_name_rejected(self):
        with self.assertRaisesRegex(TypeError, "InstalledModel"):
            reconcile_ollama_inventory([{"name": "yuki"}], self.catalog)
        model = InstalledModel("same", self.digest, 100, None, None, None)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            reconcile_ollama_inventory([model, model], self.catalog)

    def test_unmatched_catalog_artifacts_reported(self):
        report = reconcile_ollama_inventory([], self.catalog)
        self.assertEqual(report["catalog_artifacts_not_installed"], [
            {"model_id": "yuki/core", "model_version": "0.1.0"}
        ])


if __name__ == "__main__":
    unittest.main()
