import json
import unittest

from knowledge.annotation_adapters import (
    AnnotationAdapterError,
    import_doccano_export,
    import_label_studio_export,
)


class AnnotationAdapterTests(unittest.TestCase):
    def test_imports_label_studio_classification_bbox_polygon_and_text_span(self):
        tasks = [{
            "id": 7,
            "data": {"text": "Yuki protects systems"},
            "annotations": [{
                "id": "ann-1",
                "result": [
                    {"type": "choices", "value": {"choices": ["security"]}},
                    {"type": "rectanglelabels", "value": {
                        "x": 10, "y": 20, "width": 30, "height": 40,
                        "rectanglelabels": ["server"],
                    }},
                    {"type": "polygonlabels", "value": {
                        "points": [[0, 0], [100, 0], [50, 100]],
                        "polygonlabels": ["network"],
                    }},
                    {"type": "labels", "value": {
                        "start": 0, "end": 4, "labels": ["PRODUCT"],
                    }},
                ],
            }],
        }]
        dataset = import_label_studio_export(
            json.dumps(tasks).encode(), source_label="owner-export",
            imported_at="2026-11-20T10:00:00Z",
        )
        self.assertEqual([item.task_type for item in dataset.annotations],
                         ["classification", "bbox", "polygon", "text_span"])
        self.assertEqual(dataset.annotations[1].geometry, (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(dataset.annotations[3].text_value, "Yuki protects systems")
        self.assertEqual(dataset.evidence[0].source_format, "label-studio-json")
        self.assertFalse(dataset.evidence[0].verified)

    def test_skips_unsupported_label_studio_results_explicitly(self):
        tasks = [{"id": 1, "annotations": [{"result": [{"type": "brushlabels", "value": {}}]}]}]
        dataset = import_label_studio_export(
            json.dumps(tasks).encode(), source_label="test",
            imported_at="2026-11-20T10:00:00Z",
        )
        self.assertEqual(dataset.annotations, ())
        self.assertEqual(dataset.skipped_records, 1)

    def test_rejects_duplicate_tasks_and_malformed_geometry(self):
        duplicate = [{"id": 1, "annotations": []}, {"id": 1, "annotations": []}]
        with self.assertRaises(AnnotationAdapterError):
            import_label_studio_export(
                json.dumps(duplicate).encode(), source_label="test", imported_at="now",
            )
        bad = [{"id": 1, "annotations": [{"result": [{
            "type": "rectanglelabels",
            "value": {"x": 90, "y": 0, "width": 20, "height": 10, "rectanglelabels": ["x"]},
        }]}]}]
        with self.assertRaises(AnnotationAdapterError):
            import_label_studio_export(
                json.dumps(bad).encode(), source_label="test", imported_at="now",
            )

    def test_imports_doccano_spans(self):
        payload = b'{"id": 3, "text": "Alice uses Yuki", "label": [[0, 5, "PERSON"], [11, 15, "PRODUCT"]]}'
        dataset = import_doccano_export(
            payload, source_label="doccano-export",
            imported_at="2026-11-20T10:00:00Z",
        )
        self.assertEqual(len(dataset.annotations), 2)
        self.assertEqual(dataset.annotations[0].text_start, 0)
        self.assertEqual(dataset.annotations[1].label, "PRODUCT")
        self.assertEqual(dataset.evidence[0].source_format, "doccano-jsonl")

    def test_rejects_duplicate_json_keys_and_out_of_range_offsets(self):
        duplicate = b'{"id":1,"id":2,"text":"abc","label":[]}'
        with self.assertRaises(AnnotationAdapterError):
            import_doccano_export(duplicate, source_label="test", imported_at="now")
        invalid = b'{"id":1,"text":"abc","label":[[0,8,"X"]]}'
        with self.assertRaises(AnnotationAdapterError):
            import_doccano_export(invalid, source_label="test", imported_at="now")

    def test_enforces_input_limits(self):
        with self.assertRaises(AnnotationAdapterError):
            import_doccano_export(b"x" * 20, source_label="test", imported_at="now", max_bytes=10)
        with self.assertRaises(AnnotationAdapterError):
            import_label_studio_export(b"[]", source_label="test", imported_at="now", max_bytes=0)


if __name__ == "__main__":
    unittest.main()
