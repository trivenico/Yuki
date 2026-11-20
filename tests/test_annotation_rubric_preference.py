import unittest
from dataclasses import replace

from knowledge.annotation_rubric import (
    AnnotationRubric, RubricCriterion, RubricError,
    create_annotation_rubric, validate_annotation_rubric,
)
from knowledge.preference_feedback import (
    PreferenceFeedbackError, create_preference_feedback,
    validate_preference_feedback,
)


class AnnotationRubricTests(unittest.TestCase):
    def rubric(self, version=1):
        return create_annotation_rubric(
            rubric_id="response-quality", version=version,
            title="Response quality",
            instructions="Judge factual accuracy and usefulness using only the supplied prompt.",
            criteria=[
                RubricCriterion("accuracy", "Claims must be supported.", 60),
                RubricCriterion("helpfulness", "The response should solve the task.", 40),
            ],
            created_at="2026-11-20T10:00:00Z",
        )

    def test_rubric_is_versioned_immutable_and_digest_validated(self):
        first = self.rubric(1)
        second = self.rubric(2)
        self.assertTrue(validate_annotation_rubric(first))
        self.assertNotEqual(first.rubric_sha256, second.rubric_sha256)
        self.assertFalse(first.verified)
        self.assertFalse(validate_annotation_rubric(replace(first, title="tampered")))

    def test_rejects_duplicate_criteria_and_invalid_version(self):
        criterion = RubricCriterion("accuracy", "Supported claims.", 100)
        with self.assertRaises(RubricError):
            create_annotation_rubric(
                rubric_id="r", version=1, title="Rubric", instructions="Judge.",
                criteria=[criterion, criterion], created_at="2026-11-20T10:00:00Z",
            )
        with self.assertRaises(RubricError):
            self.rubric(True)

    def test_pairwise_preference_binds_prompt_responses_and_rubric(self):
        rubric = self.rubric()
        record = create_preference_feedback(
            comparison_id="cmp-1", prompt="Explain DNS safely.",
            response_a="DNS maps names to records.",
            response_b="DNS is a database of names.",
            rubric=rubric, preference="a", reviewer_label="reviewer-a",
            rationale="A is more precise and useful.",
            reviewed_at="2026-11-20T11:00:00+00:00",
        )
        self.assertTrue(validate_preference_feedback(record, rubric))
        self.assertFalse(record.verified)
        self.assertEqual(record.rubric_sha256, rubric.rubric_sha256)
        self.assertNotEqual(record.response_a_sha256, record.response_b_sha256)

    def test_preference_rejects_invalid_or_tampered_sources(self):
        rubric = self.rubric()
        args = dict(
            comparison_id="cmp-2", prompt="Prompt",
            response_a="First answer", response_b="Second answer",
            rubric=rubric, preference="b", reviewer_label="reviewer",
            rationale="B is clearer.", reviewed_at="2026-11-20T11:00:00Z",
        )
        record = create_preference_feedback(**args)
        self.assertFalse(validate_preference_feedback(replace(record, preference="a"), rubric))
        with self.assertRaises(PreferenceFeedbackError):
            create_preference_feedback(**{**args, "preference": "unknown"})
        with self.assertRaises(PreferenceFeedbackError):
            create_preference_feedback(**{**args, "response_b": "First answer"})

    def test_preference_cannot_be_reused_with_another_rubric_version(self):
        rubric1, rubric2 = self.rubric(1), self.rubric(2)
        record = create_preference_feedback(
            comparison_id="cmp-3", prompt="Prompt", response_a="A",
            response_b="B", rubric=rubric1, preference="tie",
            reviewer_label="reviewer", rationale="Equivalent quality.",
            reviewed_at="2026-11-20T11:00:00Z",
        )
        self.assertFalse(validate_preference_feedback(record, rubric2))


if __name__ == "__main__":
    unittest.main()
