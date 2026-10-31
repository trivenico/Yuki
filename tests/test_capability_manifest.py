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

    def test_specialist_orchestration_is_experimental_with_runtime_evidence(self):
        from core.capability_manifest import capability_manifest
        item = next(
            capability for capability in capability_manifest()
            if capability.capability_id == "specialist-orchestration"
        )
        self.assertEqual(item.status, "experimental")
        self.assertIn("automation/agent_coordinator.py", item.entrypoints)
        self.assertIn("automation/specialist_worker.py", item.entrypoints)
        self.assertIn("tests/test_specialist_worker.py", item.test_evidence)
        self.assertTrue(item.limitations)

    def test_third_party_skill_import_is_experimental_and_evidence_backed(self):
        item = next(
            capability for capability in DEFAULT_CAPABILITIES
            if capability.capability_id == "third-party-skill-import"
        )
        self.assertEqual(item.status, "experimental")
        self.assertIn("core/extension_store.py", item.entrypoints)
        self.assertIn("tests/test_extension_store.py", item.test_evidence)
        self.assertTrue(any("heuristic" in limitation for limitation in item.limitations))

    def test_independent_python_review_is_experimental_and_evidence_backed(self):
        item = next(
            capability for capability in DEFAULT_CAPABILITIES
            if capability.capability_id == "independent-python-review"
        )
        self.assertEqual(item.status, "experimental")
        self.assertIn("core/independent_code_review.py", item.entrypoints)
        self.assertIn("tests/test_independent_code_review.py", item.test_evidence)
        self.assertTrue(any("false positives" in limitation for limitation in item.limitations))

    def test_isolated_coding_execution_is_experimental_and_evidence_backed(self):
        item = next(
            capability for capability in DEFAULT_CAPABILITIES
            if capability.capability_id == "isolated-coding-execution"
        )
        self.assertEqual(item.status, "experimental")
        self.assertIn("core/coding_task_runner.py", item.entrypoints)
        self.assertIn("tests/test_coding_task_runner.py", item.test_evidence)
        self.assertTrue(any("trusted Docker daemon" in limitation for limitation in item.limitations))

    def test_typed_tool_execution_is_experimental_and_evidence_backed(self):
        item = next(
            capability for capability in DEFAULT_CAPABILITIES
            if capability.capability_id == "typed-tool-execution"
        )
        self.assertEqual(item.status, "experimental")
        self.assertIn("core/typed_tool_runtime.py", item.entrypoints)
        self.assertIn("tests/test_typed_tool_runtime.py", item.test_evidence)
        self.assertTrue(any("not a sandbox" in limitation for limitation in item.limitations))

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


if __name__ == "__main__":
    unittest.main()
