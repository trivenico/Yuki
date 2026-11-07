import unittest

from providers.dataset_quality import assess_dataset_quality
from providers.dataset_records import DatasetRecordError


class DatasetQualityTests(unittest.TestCase):
    def test_clean_dataset_passes_without_exposing_text(self):
        report = assess_dataset_quality([
            {"prompt": "Explain gravity", "response": "Mass attracts mass."},
            {"prompt": "Explain photosynthesis", "response": "Plants convert light."},
        ])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["record_count"], 2)
        self.assertNotIn("Explain gravity", repr(report))
        self.assertFalse(report["source_modified"])
        self.assertFalse(report["legal_rights_verified"])

    def test_duplicate_records_are_reported_for_review(self):
        report = assess_dataset_quality([
            {"prompt": "hello", "response": "world"},
            {"prompt": "hello", "response": "world"},
        ])
        self.assertEqual(report["duplicate_record_count"], 1)
        self.assertEqual(report["status"], "review_required")

    def test_potential_pii_blocks_dataset_and_never_echoes_value(self):
        secret = "person@example.com"
        report = assess_dataset_quality([
            {"prompt": "Contact " + secret, "response": "Please help."},
        ])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("potential_sensitive_data", repr(report))
        self.assertNotIn(secret, repr(report))

    def test_credential_like_value_blocks_dataset(self):
        report = assess_dataset_quality([
            {"prompt": "configure api_key=AbCdEf0123456789", "response": "done"},
        ])
        self.assertEqual(report["status"], "blocked")

    def test_length_limits_block_oversized_text(self):
        report = assess_dataset_quality(
            [{"prompt": "long prompt", "response": "answer"}],
            max_prompt_chars=4,
        )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["oversized_record_count"], 1)

    def test_rejects_empty_or_invalid_inputs(self):
        with self.assertRaisesRegex(DatasetRecordError, "nonempty_records_required"):
            assess_dataset_quality([])
        with self.assertRaisesRegex(DatasetRecordError, "invalid_max_prompt_chars"):
            assess_dataset_quality([{"prompt": "x", "response": "y"}], max_prompt_chars=True)


if __name__ == "__main__":
    unittest.main()
