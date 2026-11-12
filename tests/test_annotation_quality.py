import unittest

from core.annotation_quality import assess_annotation_quality, validate_quality_policy


class AnnotationQualityTests(unittest.TestCase):
    def test_policy_rejects_unknown_fields_invalid_ranges_and_duplicate_labels(self):
        for policy in (
            {"mystery": True},
            {"minimum_confidence": float("nan")},
            {"minimum_items": 4, "maximum_items": 2},
            {"required_labels": ["bird", "bird"]},
        ):
            with self.assertRaises(ValueError):
                validate_quality_policy({"quality": policy})

    def test_classification_flags_low_confidence_missing_rationale_and_required_label(self):
        result = assess_annotation_quality(
            "image_classification",
            {"label": "bird", "confidence": 0.4},
            {"labels": ["bird"], "quality": {
                "minimum_confidence": 0.8, "require_rationale": True,
                "required_labels": ["bird", "mammal"],
            }},
        )
        self.assertEqual(result["status"], "warning")
        self.assertEqual(
            {item["code"] for item in result["findings"]},
            {"confidence_below_threshold", "rationale_missing", "required_labels_missing"},
        )
        self.assertEqual(result["metrics"], {"annotation_items": 1, "distinct_labels": 1})

    def test_bounding_box_item_limits_are_measured_deterministically(self):
        result = assess_annotation_quality(
            "bounding_boxes",
            {"boxes": [{"label": "car", "x": 0, "y": 0, "width": 0.2, "height": 0.2}]},
            {"quality": {"minimum_items": 2, "maximum_items": 4}},
        )
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["findings"][0]["code"], "annotation_count_below_minimum")

    def test_clean_result_passes_without_claiming_semantic_correctness(self):
        result = assess_annotation_quality(
            "text_classification", {"label": "positive", "rationale": "The wording is positive."},
            {"labels": ["positive"], "quality": {"require_rationale": True}},
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["policy_version"], 1)

    def test_quality_findings_do_not_mutate_submission_payload(self):
        payload = {"label": "bird", "confidence": 0.2}
        before = dict(payload)
        assess_annotation_quality(
            "image_classification", payload, {"quality": {"minimum_confidence": 0.8}}
        )
        self.assertEqual(payload, before)


if __name__ == "__main__":
    unittest.main()
