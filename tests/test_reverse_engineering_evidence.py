import copy
import unittest

from reverse_engineering.evidence import (
    EvidenceError,
    create_evidence_record,
    evidence_summary,
    verify_evidence_record,
)


class EvidenceContractTests(unittest.TestCase):
    def make_record(self):
        return create_evidence_record(
            operation="inspect_application",
            target={"kind": "directory", "label": "authorized-app", "sha256": "a" * 64},
            provider={"name": "yuki-static-inspector", "version": "0.1"},
            observed_at="2026-11-07T00:00:00Z",
            normalized_result={"modules": ["main.js"], "imports": 1},
            limitations=["Static inspection does not prove runtime behavior."],
            coverage={"status": "partial", "scope": "observed-files"},
            provenance={"adapter": "static-js", "adapter_version": "0.1"},
        )

    def test_record_is_content_addressed_and_verifiable(self):
        record = self.make_record()
        self.assertTrue(verify_evidence_record(record))
        self.assertEqual(len(record["evidence_id"]), 64)
        self.assertEqual(record["evidence_id"], self.make_record()["evidence_id"])

    def test_mutation_is_detected(self):
        record = self.make_record()
        changed = copy.deepcopy(record)
        changed["normalized_result"]["imports"] = 99
        with self.assertRaisesRegex(EvidenceError, "integrity"):
            verify_evidence_record(changed)

    def test_unknown_envelope_fields_are_rejected(self):
        record = self.make_record()
        record["trusted"] = True
        with self.assertRaisesRegex(EvidenceError, "unsupported"):
            verify_evidence_record(record)

    def test_invalid_target_digest_is_rejected(self):
        with self.assertRaisesRegex(EvidenceError, "SHA-256"):
            create_evidence_record(
                operation="inspect_binary",
                target={"kind": "binary", "label": "sample", "sha256": "not-a-digest"},
                provider={"name": "static", "version": "1"},
                observed_at="2026-11-07T00:00:00Z",
                normalized_result={},
            )

    def test_unknown_coverage_is_not_promoted_to_complete(self):
        record = self.make_record()
        self.assertEqual(record["coverage"]["status"], "partial")
        self.assertEqual(evidence_summary(record)["limitation_count"], 1)

    def test_non_finite_numbers_are_rejected(self):
        with self.assertRaises(EvidenceError):
            self.make_record_with_result({"measurement": float("nan")})

    def test_nested_payload_is_bounded(self):
        with self.assertRaisesRegex(EvidenceError, "nesting"):
            self.make_record_with_result({"nested": [[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]})

    def make_record_with_result(self, result):
        return create_evidence_record(
            operation="inspect_artifact",
            target={"kind": "file", "label": "sample", "sha256": "b" * 64},
            provider={"name": "static", "version": "1"},
            observed_at="2026-11-07T00:00:00Z",
            normalized_result=result,
        )


if __name__ == "__main__":
    unittest.main()
