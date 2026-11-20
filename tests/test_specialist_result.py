import unittest

from automation.specialist_result import SpecialistResult, validate_specialist_result


class SpecialistResultTests(unittest.TestCase):
    def test_serializes_versioned_provenance_envelope(self):
        payload = SpecialistResult("reviewer", "Looks reasonable.").to_payload()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["provenance"]["evidence_basis"], "supplied_material_only")
        self.assertFalse(payload["provenance"]["verified_externally"])
        self.assertEqual(payload["execution"], "text-only")
        self.assertTrue(payload["limitations"])

    def test_rejects_external_verification_claim(self):
        result = SpecialistResult("reviewer", "Claim", verified_externally=True)
        with self.assertRaises(ValueError):
            validate_specialist_result(result)

    def test_rejects_unsupported_evidence_basis(self):
        result = SpecialistResult("reviewer", "Claim", evidence_basis="web")
        with self.assertRaises(ValueError):
            validate_specialist_result(result)

    def test_rejects_unbounded_or_empty_output(self):
        with self.assertRaises(ValueError):
            SpecialistResult("reviewer", " ").to_payload()
        with self.assertRaises(ValueError):
            SpecialistResult("reviewer", "x" * 12_001).to_payload()


if __name__ == "__main__":
    unittest.main()
