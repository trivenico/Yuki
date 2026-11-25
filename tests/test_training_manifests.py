import copy
import unittest

from evaluation.training_manifests import (
    ManifestValidationError,
    validate_model_candidate_manifest,
    validate_training_dataset_manifest,
)


SHA1 = "a" * 40
SHA256 = "b" * 64


def dataset_manifest():
    return {
        "schema_version": 1,
        "dataset_id": "public-reasoning-v1",
        "hub_repository": "yuki-evals/reasoning",
        "revision": SHA1,
        "license": {
            "spdx_id": "MIT", "review_status": "approved",
            "reviewed_by": "reviewer", "reviewed_at": "2026-11-20T12:00:00Z",
            "evidence_url": "https://huggingface.co/datasets/yuki-evals/reasoning",
        },
        "source_url": "https://huggingface.co/datasets/yuki-evals/reasoning",
        "retrieved_at": "2026-11-20T12:00:00Z",
        "splits": ["train", "validation"],
        "intended_use": "Evaluate assistant reasoning quality.",
        "content_sha256": SHA256,
        "privacy_review": {
            "status": "approved", "contains_personal_data": False,
            "reviewed_at": "2026-11-20T12:00:00Z",
        },
    }


def candidate_manifest():
    return {
        "schema_version": 1,
        "candidate_id": "yuki-candidate-001",
        "base_model": "org/base-model",
        "base_revision": SHA1,
        "training_code_commit": SHA1,
        "dataset_manifests": ["manifests/reasoning.json"],
        "artifact": {
            "uri": "https://huggingface.co/yuki/model/resolve/abc/model.safetensors",
            "sha256": SHA256, "format": "safetensors", "size_bytes": 1024,
        },
        "training": {
            "platform": "kaggle", "runtime_image": "kaggle/python:latest",
            "python_version": "3.11.8", "seed": 42, "config_sha256": SHA256,
            "started_at": "2026-11-20T12:00:00Z",
            "completed_at": "2026-11-20T13:00:00Z", "accelerator": "GPU",
        },
        "evaluation": {
            "report_uri": "https://huggingface.co/yuki/reports/report.json",
            "report_sha256": SHA256, "suite_revision": SHA1, "status": "passed",
        },
        "reviews": {
            "license": "approved", "security": "approved",
            "human_approval": "approved", "reviewed_by": "reviewer",
            "reviewed_at": "2026-11-20T14:00:00Z",
        },
        "rollback_candidate": "yuki-candidate-000",
    }


class DatasetManifestTests(unittest.TestCase):
    def test_valid_manifest(self):
        self.assertEqual(validate_training_dataset_manifest(dataset_manifest())["dataset_id"], "public-reasoning-v1")

    def test_rejects_unpinned_revision_and_unapproved_reviews(self):
        for mutate in (
            lambda item: item.update(revision="main"),
            lambda item: item["license"].update(review_status="pending"),
            lambda item: item["privacy_review"].update(status="rejected"),
        ):
            item = dataset_manifest()
            mutate(item)
            with self.subTest(item=item), self.assertRaises(ManifestValidationError):
                validate_training_dataset_manifest(item)

    def test_rejects_personal_data_by_default(self):
        item = dataset_manifest()
        item["privacy_review"]["contains_personal_data"] = True
        with self.assertRaisesRegex(ManifestValidationError, "personal data"):
            validate_training_dataset_manifest(item, require_approved=False)

    def test_rejects_duplicate_splits_unknown_fields_and_non_utc_timestamps(self):
        cases = []
        item = dataset_manifest(); item["splits"] = ["train", "train"]; cases.append(item)
        item = dataset_manifest(); item["unexpected"] = True; cases.append(item)
        item = dataset_manifest(); item["retrieved_at"] = "2026-11-20T12:00:00"; cases.append(item)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ManifestValidationError):
                validate_training_dataset_manifest(item)


class ModelCandidateManifestTests(unittest.TestCase):
    def test_valid_candidate_and_explicit_qualification(self):
        item = candidate_manifest()
        self.assertIs(validate_model_candidate_manifest(item), item)
        self.assertIs(validate_model_candidate_manifest(item, require_qualified=True), item)

    def test_structural_validation_does_not_imply_qualification(self):
        item = candidate_manifest()
        item["reviews"]["human_approval"] = "pending"
        self.assertIs(validate_model_candidate_manifest(item), item)
        with self.assertRaisesRegex(ManifestValidationError, "review gates"):
            validate_model_candidate_manifest(item, require_qualified=True)

    def test_rejects_traversal_and_self_rollback(self):
        for mutate in (
            lambda item: item.update(dataset_manifests=["../../secrets.json"]),
            lambda item: item.update(rollback_candidate=item["candidate_id"]),
        ):
            item = candidate_manifest()
            mutate(item)
            with self.subTest(item=item), self.assertRaises(ManifestValidationError):
                validate_model_candidate_manifest(item)

    def test_rejects_bad_digest_uri_and_time_order(self):
        cases = []
        item = candidate_manifest(); item["artifact"]["sha256"] = "0"; cases.append(item)
        item = candidate_manifest(); item["artifact"]["uri"] = "file:///tmp/model"; cases.append(item)
        item = candidate_manifest(); item["training"]["completed_at"] = "2026-11-20T11:00:00Z"; cases.append(item)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ManifestValidationError):
                validate_model_candidate_manifest(item)

    def test_rejects_boolean_seed_and_boolean_schema_version(self):
        for field, value in (("seed", True),):
            item = candidate_manifest(); item["training"][field] = value
            with self.subTest(field=field), self.assertRaises(ManifestValidationError):
                validate_model_candidate_manifest(item)
        item = candidate_manifest(); item["schema_version"] = True
        with self.assertRaises(ManifestValidationError):
            validate_model_candidate_manifest(item)


if __name__ == "__main__":
    unittest.main()
