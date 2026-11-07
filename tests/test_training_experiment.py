import hashlib
import unittest

from providers.dataset_records import deduplicate_dataset_records, deterministic_dataset_split
from providers.dataset_quality import assess_dataset_quality
from providers.dataset_artifact_manifest import DatasetManifestError
from providers.training_experiment import (
    TrainingExperimentError,
    create_experiment_spec,
    verify_experiment_spec,
    build_training_preflight,
)


def approved_manifest():
    item = {
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
    item.update({
        "rights_status": "verified",
        "consent_status": "verified",
        "privacy_status": "verified",
        "review_status": "approved",
    })
    return item


def split_manifest():
    records = deduplicate_dataset_records([
        {"prompt": f"prompt-{i}", "response": f"response-{i}"} for i in range(80)
    ])["records"]
    return {"schema_version": 1, **deterministic_dataset_split(records, seed="train-exp-1")}


def clean_quality_report():
    return assess_dataset_quality([
        {"prompt": "Explain gravity", "response": "Mass attracts mass."},
        {"prompt": "Explain photosynthesis", "response": "Plants convert light."},
    ])


def make_spec(**overrides):
    args = {
        "experiment_id": "yuki-core-exp-001",
        "model_id": "yuki/core",
        "model_version": "0.1.0",
        "training_method": "lora",
        "base_model_sha256": "b" * 64,
        "base_model_evidence": {
            "model_id": "org/pretrained-model",
            "source_uri": "https://huggingface.co/org/pretrained-model",
            "source_revision": "0123456789abcdef",
            "artifact_sha256": "b" * 64,
            "license_id": "Apache-2.0",
            "license_evidence": "review-ticket:MODEL-001",
            "rights_status": "verified",
            "fine_tuning_allowed": True,
            "commercial_use_allowed": True,
        },
        "datasets": [approved_manifest()],
        "split_manifest": split_manifest(),
        "benchmark_id": "yuki-core-quality",
        "benchmark_version": "1.0",
        "baseline_evaluation_sha256": "c" * 64,
        "seed": 42,
        "hyperparameters": {
            "epochs": 2, "learning_rate": 0.0002, "batch_size": 4,
            "gradient_accumulation_steps": 8, "max_sequence_length": 2048,
            "lora_rank": 16, "lora_alpha": 32, "lora_dropout": 0.05,
        },
        "created_at": "2026-11-05T10:00:00Z",
    }
    args.update(overrides)
    return create_experiment_spec(**args)


class TrainingExperimentTests(unittest.TestCase):
    def test_creates_content_addressed_spec_and_verifies(self):
        spec = make_spec()
        self.assertTrue(verify_experiment_spec(spec))
        self.assertEqual(spec["execution_policy"], "manual_training_approval_required")
        self.assertEqual(spec["activation_policy"], "manual_model_release_approval_required")

    def test_spec_is_deterministic_for_same_inputs(self):
        self.assertEqual(make_spec()["experiment_sha256"], make_spec()["experiment_sha256"])

    def test_rejects_unapproved_base_model_rights(self):
        evidence = make_spec()["base_model_evidence"]
        evidence["fine_tuning_allowed"] = False
        with self.assertRaisesRegex(TrainingExperimentError, "base_model_rights_not_approved"):
            make_spec(base_model_evidence=evidence)

    def test_rejects_base_model_digest_mismatch(self):
        evidence = make_spec()["base_model_evidence"]
        evidence["artifact_sha256"] = "d" * 64
        with self.assertRaisesRegex(TrainingExperimentError, "base_model_digest_mismatch"):
            make_spec(base_model_evidence=evidence)

    def test_rejects_from_scratch_training(self):
        with self.assertRaisesRegex(TrainingExperimentError, "invalid_training_method"):
            make_spec(training_method="from_scratch")

    def test_rejects_unapproved_dataset(self):
        item = approved_manifest()
        item["rights_status"] = "unverified"
        with self.assertRaisesRegex(TrainingExperimentError, "dataset_not_approved"):
            make_spec(datasets=[item])

    def test_rejects_split_digest_tampering_and_leakage(self):
        split = split_manifest()
        split["split_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(TrainingExperimentError, "split_manifest_digest_mismatch"):
            make_spec(split_manifest=split)
        split = split_manifest()
        first = split["splits"]["train"][0]
        split["splits"]["validation"].append(first)
        payload = {key: value for key, value in split.items()
                   if key not in {"split_manifest_sha256", "schema_version"}}
        split["split_manifest_sha256"] = hashlib.sha256(
            __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.assertRaisesRegex(TrainingExperimentError, "split_leakage_detected"):
            make_spec(split_manifest=split)

    def test_rejects_invalid_lora_and_qlora_parameters(self):
        with self.assertRaisesRegex(TrainingExperimentError, "lora_configuration_required"):
            make_spec(hyperparameters={"epochs": 1})
        with self.assertRaisesRegex(TrainingExperimentError, "qlora_requires_four_bit"):
            make_spec(training_method="qlora", hyperparameters={
                "epochs": 1, "lora_rank": 16, "lora_alpha": 32, "quantization_bits": 8,
            })

    def test_rejects_invalid_seed_and_unknown_parameters(self):
        with self.assertRaisesRegex(TrainingExperimentError, "invalid_seed"):
            make_spec(seed=True)
        with self.assertRaisesRegex(TrainingExperimentError, "invalid_hyperparameters"):
            make_spec(hyperparameters={"epochs": 1, "shell_command": "run me"})


    def test_training_preflight_reports_fit_without_dispatching(self):
        spec = make_spec()
        worker = {
            "worker_id": "gpu-worker-01", "online": True, "paired": True,
            "enabled": True, "backends": ["transformers"],
            "ram_available_bytes": 32 * 1024**3,
            "storage_available_bytes": 100 * 1024**3,
        }
        report = build_training_preflight(
            spec, worker=worker, backend="transformers",
            estimated_peak_memory_bytes=24 * 1024**3,
            required_storage_bytes=20 * 1024**3,
            estimated_cost=2.5, budget_limit=5.0,
            quality_report=clean_quality_report(),
        )
        self.assertEqual(report["status"], "preflight_passed")
        self.assertFalse(report["execution_dispatched"])
        self.assertTrue(report["approval_required"])
        self.assertTrue(report["estimates_are_advisory"])

    def test_training_preflight_fails_closed_for_resource_and_trust_gaps(self):
        worker = {
            "worker_id": "gpu-worker-01", "online": False, "paired": False,
            "enabled": False, "backends": ["cpu"],
            "ram_available_bytes": 1024,
            "storage_available_bytes": 1024,
        }
        report = build_training_preflight(
            make_spec(), worker=worker, backend="transformers",
            estimated_peak_memory_bytes=2048, required_storage_bytes=2048,
            estimated_cost=10, budget_limit=1,
            quality_report=clean_quality_report(),
        )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(
            set(report["blockers"]),
            {"worker_offline", "worker_not_trusted_or_disabled",
             "backend_not_supported", "insufficient_memory",
             "insufficient_storage", "budget_limit_exceeded"},
        )

    def test_training_preflight_rejects_malformed_worker_metadata(self):
        worker = {
            "worker_id": "gpu-worker-01", "online": True, "paired": True,
            "enabled": True, "backends": ["transformers"],
            "ram_available_bytes": True, "storage_available_bytes": 1024,
        }
        with self.assertRaisesRegex(TrainingExperimentError, "invalid_preflight_available_memory_bytes"):
            build_training_preflight(
                make_spec(), worker=worker, backend="transformers",
                estimated_peak_memory_bytes=512, required_storage_bytes=512,
                estimated_cost=0, budget_limit=1,
                quality_report=clean_quality_report(),
            )


    def test_training_preflight_blocks_dataset_quality_findings(self):
        worker = {
            "worker_id": "gpu-worker-01", "online": True, "paired": True,
            "enabled": True, "backends": ["transformers"],
            "ram_available_bytes": 32 * 1024**3,
            "storage_available_bytes": 100 * 1024**3,
        }
        unsafe_report = assess_dataset_quality([
            {"prompt": "Email me at person@example.com", "response": "Help."},
        ])
        with self.assertRaisesRegex(TrainingExperimentError, "dataset_quality_not_approved"):
            build_training_preflight(
                make_spec(), worker=worker, backend="transformers",
                estimated_peak_memory_bytes=1024, required_storage_bytes=1024,
                estimated_cost=0, budget_limit=1, quality_report=unsafe_report,
            )

    def test_training_preflight_binds_quality_report_digest(self):
        report = clean_quality_report()
        worker = {
            "worker_id": "gpu-worker-01", "online": True, "paired": True,
            "enabled": True, "backends": ["transformers"],
            "ram_available_bytes": 32 * 1024**3,
            "storage_available_bytes": 100 * 1024**3,
        }
        result = build_training_preflight(
            make_spec(), worker=worker, backend="transformers",
            estimated_peak_memory_bytes=1024, required_storage_bytes=1024,
            estimated_cost=0, budget_limit=1, quality_report=report,
        )
        self.assertEqual(result["dataset_quality_content_sha256"], report["content_sha256"])
        self.assertEqual(result["dataset_quality_status"], "passed")

    def test_detects_spec_mutation(self):
        spec = make_spec()
        spec["hyperparameters"]["epochs"] = 99
        with self.assertRaisesRegex(TrainingExperimentError, "experiment_digest_mismatch"):
            verify_experiment_spec(spec)


if __name__ == "__main__":
    unittest.main()
