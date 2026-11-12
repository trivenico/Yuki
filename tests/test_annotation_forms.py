"""Tests for task-family annotation editor metadata and payload conversion."""
import unittest

from ui.annotation_forms import annotation_editor_fields, build_annotation_payload


class AnnotationEditorFormTests(unittest.TestCase):
    def test_classification_uses_rubric_choices_and_typed_optional_fields(self):
        fields = annotation_editor_fields("image_classification", {"labels": ["cat", "dog"]})
        label = next(field for field in fields if field.name == "label")
        self.assertEqual(label.kind, "choice")
        self.assertEqual(label.options, ("cat", "dog"))
        payload = build_annotation_payload(
            "image_classification",
            {"label": "cat", "confidence": "0.95", "rationale": "Visible whiskers"},
            {"labels": ["cat", "dog"]},
        )
        self.assertEqual(payload["confidence"], 0.95)

    def test_bbox_form_converts_json_and_delegates_validation(self):
        payload = build_annotation_payload("bounding_boxes", {
            "boxes": '[{"label":"car","x":0.1,"y":0.2,"width":0.3,"height":0.4}]'
        }, {"labels": ["car"]})
        self.assertEqual(payload["boxes"][0]["label"], "car")
        with self.assertRaisesRegex(ValueError, "bounding_box_out_of_bounds"):
            build_annotation_payload("bounding_boxes", {
                "boxes": '[{"label":"car","x":0.9,"y":0.2,"width":0.3,"height":0.4}]'
            }, {"labels": ["car"]})

    def test_nested_task_families_use_distinct_field_contracts(self):
        cases = [
            ("polygon_segmentation", {"polygons": '[{"label":"road","points":[[0,0],[1,0],[0,1]]}]'}, {"labels": ["road"]}),
            ("video_tracking", {"tracks": '[{"track_id":"t1","label":"car","frames":[{"frame":0,"x":0,"y":0,"width":0.2,"height":0.2}]}]'}, {"labels": ["car"]}),
            ("audio_transcription", {"segments": '[{"start":0,"end":1,"text":"hello"}]'}, {}),
            ("speaker_diarization", {"segments": '[{"start":0,"end":1,"text":"hello","speaker":"s1"}]'}, {}),
            ("entity_extraction", {"entities": '[{"text":"Ada","label":"PERSON","start":0,"end":3}]'}, {"source_text": "Ada Lovelace"}),
            ("response_ranking", {"ranking": '[{"response_id":"a","rank":1},{"response_id":"b","rank":2}]'}, {}),
            ("llm_evaluation", {"scores": '{"accuracy":4.5}'}, {}),
        ]
        for task_type, values, rubric in cases:
            with self.subTest(task_type=task_type):
                payload = build_annotation_payload(task_type, values, rubric)
                self.assertTrue(payload)

    def test_required_fields_invalid_json_and_unexpected_fields_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "required_editor_field"):
            build_annotation_payload("audio_transcription", {})
        with self.assertRaisesRegex(ValueError, "invalid_editor_json"):
            build_annotation_payload("bounding_boxes", {"boxes": "not-json"})
        with self.assertRaisesRegex(ValueError, "unexpected_editor_field"):
            build_annotation_payload("image_classification", {"label": "cat", "shell": "true"})

    def test_custom_payload_remains_object_and_validates_size(self):
        self.assertEqual(build_annotation_payload("custom", {"payload": '{"answer":42}'}), {"answer": 42})
        with self.assertRaisesRegex(ValueError, "invalid_editor_json_type"):
            build_annotation_payload("custom", {"payload": '["not","object"]'})

    def test_unknown_task_family_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported_annotation_task_type"):
            annotation_editor_fields("future_task")


if __name__ == "__main__":
    unittest.main()
