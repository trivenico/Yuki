import unittest

from core.annotation_validation import validate_annotation_payload


class AnnotationValidationTests(unittest.TestCase):
    def test_classification_enforces_rubric_labels_and_confidence(self):
        validate_annotation_payload("image_classification", {"label": "bird", "confidence": 0.9},
                                    {"labels": ["bird", "cat"]})
        for payload in ({"label": "dog"}, {"label": "bird", "confidence": 1.1},
                        {"label": "bird", "confidence": True}):
            with self.assertRaises(ValueError):
                validate_annotation_payload("image_classification", payload, {"labels": ["bird", "cat"]})

    def test_bounding_boxes_must_be_normalized_and_inside_image(self):
        validate_annotation_payload("bounding_boxes", {"boxes": [
            {"label": "car", "x": 0.1, "y": 0.2, "width": 0.4, "height": 0.5}
        ]}, {"labels": ["car"]})
        with self.assertRaisesRegex(ValueError, "bounding_box_out_of_bounds"):
            validate_annotation_payload("bounding_boxes", {"boxes": [
                {"label": "car", "x": 0.8, "y": 0.2, "width": 0.4, "height": 0.5}
            ]}, {"labels": ["car"]})

    def test_polygon_requires_three_normalized_points(self):
        validate_annotation_payload("polygon_segmentation", {"polygons": [
            {"label": "road", "points": [[0, 0], [1, 0], [0.5, 1]]}
        ]}, {"labels": ["road"]})
        with self.assertRaises(ValueError):
            validate_annotation_payload("polygon_segmentation", {"polygons": [
                {"label": "road", "points": [[0, 0], [1, 0]]}
            ]}, {"labels": ["road"]})

    def test_audio_segments_require_valid_timing_and_speaker_when_requested(self):
        validate_annotation_payload("speaker_diarization", {"segments": [
            {"start": 0, "end": 1.5, "text": "hello", "speaker": "speaker-1"}
        ]})
        with self.assertRaises(ValueError):
            validate_annotation_payload("speaker_diarization", {"segments": [
                {"start": 2, "end": 1, "text": "hello", "speaker": "speaker-1"}
            ]})

    def test_entity_offsets_must_match_source_and_not_overlap(self):
        validate_annotation_payload("entity_extraction", {"entities": [
            {"text": "Alice", "label": "PERSON", "start": 0, "end": 5}
        ]}, {"source_text": "Alice arrived"})
        with self.assertRaisesRegex(ValueError, "entity_offsets_do_not_match_source"):
            validate_annotation_payload("entity_extraction", {"entities": [
                {"text": "Bob", "label": "PERSON", "start": 0, "end": 3}
            ]}, {"source_text": "Alice arrived"})

    def test_response_ranking_requires_unique_contiguous_ranks(self):
        validate_annotation_payload("response_ranking", {"ranking": [
            {"response_id": "a", "rank": 1}, {"response_id": "b", "rank": 2}
        ]})
        with self.assertRaises(ValueError):
            validate_annotation_payload("response_ranking", {"ranking": [
                {"response_id": "a", "rank": 1}, {"response_id": "b", "rank": 1}
            ]})

    def test_llm_evaluation_scores_are_bounded_and_finite(self):
        validate_annotation_payload("llm_evaluation", {"scores": {"accuracy": 4, "style": 3.5}})
        with self.assertRaises(ValueError):
            validate_annotation_payload("llm_evaluation", {"scores": {"accuracy": float("nan")}})

    def test_custom_projects_keep_json_object_contract(self):
        validate_annotation_payload("custom", {"anything": [1, 2, 3]})


if __name__ == "__main__":
    unittest.main()
