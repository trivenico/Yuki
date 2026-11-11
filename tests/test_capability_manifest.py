import unittest
from dataclasses import replace
from pathlib import Path

from core.capability_manifest import (
    CAPABILITY_STATUSES,
    DEFAULT_CAPABILITIES,
    CapabilityContract,
    manifest_as_dicts,
    validate_manifest,
)


class CapabilityManifestTests(unittest.TestCase):
    def test_default_manifest_is_valid_and_evidence_exists(self):
        self.assertEqual(validate_manifest(repository_root=Path(__file__).resolve().parents[1]), [])

    def test_capability_ids_are_unique_and_statuses_are_valid(self):
        ids = [item.capability_id for item in DEFAULT_CAPABILITIES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(item.status in CAPABILITY_STATUSES for item in DEFAULT_CAPABILITIES))

    def test_specialist_capabilities_match_verified_entrypoints_and_limits(self):
        from core.capability_manifest import capability_manifest

        contracts = {item.capability_id: item for item in capability_manifest()}
        self.assertEqual(contracts["specialist-orchestration"].status, "experimental")
        self.assertEqual(contracts["specialist-worker"].status, "experimental")
        self.assertEqual(contracts["specialist-read-tools"].status, "experimental")
        self.assertIn("automation/agent_coordinator.py",
                      contracts["specialist-orchestration"].entrypoints)
        self.assertIn("tests/test_specialist_worker.py",
                      contracts["specialist-worker"].test_evidence)
        self.assertTrue(any("No write" in item for item in
                            contracts["specialist-read-tools"].limitations))

    def test_implemented_capability_requires_test_evidence(self):
        contract = CapabilityContract(
            capability_id="test-capability", name="Example", status="implemented",
            summary="An implemented contract", entrypoints=("example.py",),
            dependencies=(), permissions=(), side_effects=(), deployments=("local",),
            risk="low", test_evidence=(),
        )
        self.assertTrue(any("require test evidence" in error for error in contract.validate()))

    def test_invalid_status_risk_and_deployment_are_rejected(self):
        contract = replace(
            DEFAULT_CAPABILITIES[0], status="magic", risk="infinite",
            deployments=("internet",),
        )
        errors = contract.validate()
        self.assertTrue(any("unsupported status" in error for error in errors))
        self.assertTrue(any("unsupported risk" in error for error in errors))
        self.assertTrue(any("deployments" in error for error in errors))

    def test_missing_evidence_file_fails_validation(self):
        contract = replace(DEFAULT_CAPABILITIES[0], test_evidence=("tests/not_a_real_test.py",))
        self.assertTrue(any("does not exist" in error for error in validate_manifest(
            [contract], repository_root=Path(__file__).resolve().parents[1]
        )))

    def test_manifest_export_is_json_safe(self):
        from json import dumps, loads
        result = loads(dumps(manifest_as_dicts()))
        self.assertEqual(result[0]["capability_id"], "skill-registry")
        self.assertIsInstance(result[0]["entrypoints"], list)


    def test_product_surface_inventory_is_complete_and_conservative(self):
        from core.capability_manifest import capability_manifest

        contracts = {item.capability_id: item for item in capability_manifest()}
        expected = {
            "provider-runtime", "conversation-api", "companion-foundation",
            "cli-assistant", "model-registry-evaluation", "paired-device-inference",
            "lan-call-sessions", "income-opportunity-pipeline",
            "native-desktop-application", "end-to-end-voice",
        }
        self.assertTrue(expected.issubset(contracts))
        self.assertEqual(contracts["native-desktop-application"].status, "planned")
        self.assertEqual(contracts["income-opportunity-pipeline"].status, "experimental")
        self.assertEqual(contracts["lan-call-sessions"].status, "experimental")
        self.assertTrue(any("WebRTC" in item for item in contracts["lan-call-sessions"].limitations))
        self.assertTrue(any("No live platform adapter" in item
                            for item in contracts["income-opportunity-pipeline"].limitations))
        self.assertTrue(any("not prove a persistent companion loop" in item
                            for item in contracts["companion-foundation"].limitations))

    def test_all_product_contract_evidence_paths_exist(self):
        from core.capability_manifest import capability_manifest

        errors = validate_manifest(
            capability_manifest(), repository_root=Path(__file__).resolve().parents[1]
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
