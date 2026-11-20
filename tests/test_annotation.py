import json
import unittest

from knowledge.annotation import AnnotationImportError, import_annotation_jsonl


class AnnotationImportTests(unittest.TestCase):
    def parse(self, row, **kwargs):
        return import_annotation_jsonl(
            (json.dumps(row) + "\n").encode(),
            source_format="yuki-jsonl",
            source_label="owner_dataset",
            imported_at="2026-11-20T10:00:00Z",
            **kwargs,
        )

    def test_imports_classification_bbox_polygon_and_text_span(self):
        rows = [
            {"annotation_id": "a1", "task_type": "classification", "label": "cat", "asset_id": "img-1"},
            {"annotation_id": "a2", "task_type": "bbox", "label": "cat", "asset_id": "img-1", "geometry": [0.1, 0.2, 0.3, 0.4]},
            {"annotation_id": "a3", "task_type": "polygon", "label": "road", "asset_id": "img-2", "geometry": [0, 0, 1, 0, 0.5, 1]},
            {"annotation_id": "a4", "task_type": "text_span", "label": "PERSON", "asset_id": "doc-1", "text_start": 0, "text_end": 4, "text_value": "John visited"},
        ]
        payload = b"\n".join(json.dumps(row).encode() for row in rows)
        dataset = import_annotation_jsonl(
            payload, source_format="normalized", source_label="test",
            imported_at="2026-11-20T10:00:00Z",
        )
        self.assertEqual(len(dataset.annotations), 4)
        self.assertEqual(dataset.annotations[1].geometry, (0.1, 0.2, 0.3, 0.4))
        self.assertFalse(dataset.evidence[0].verified)
        self.assertEqual(dataset.evidence[0].provenance, "owner_supplied_unverified")

    def test_rejects_duplicate_ids_unknown_fields_and_duplicate_json_keys(self):
        row = {"annotation_id": "a", "task_type": "classification", "label": "ok", "asset_id": "x"}
        duplicate_payload = (json.dumps(row) + "\\n" + json.dumps(row)).encode()
        with self.assertRaises(AnnotationImportError):
            import_annotation_jsonl(duplicate_payload, source_format="x", source_label="y", imported_at="z")
        with self.assertRaises(AnnotationImportError):
            self.parse({**row, "surprise": True})
        raw = b'{"annotation_id":"a","annotation_id":"b","task_type":"classification","label":"x","asset_id":"i"}'
        with self.assertRaises(AnnotationImportError):
            import_annotation_jsonl(raw, source_format="x", source_label="y", imported_at="z")

    def test_rejects_invalid_geometry_and_text_offsets(self):
        bbox = {"annotation_id": "a", "task_type": "bbox", "label": "x", "asset_id": "i", "geometry": [0.8, 0.8, 0.5, 0.5]}
        with self.assertRaises(AnnotationImportError):
            self.parse(bbox)
        span = {"annotation_id": "b", "task_type": "text_span", "label": "x", "asset_id": "d", "text_start": 2, "text_end": 1, "text_value": "hello"}
        with self.assertRaises(AnnotationImportError):
            self.parse(span)

    def test_rejects_oversized_input_and_invalid_limits(self):
        with self.assertRaises(AnnotationImportError):
            import_annotation_jsonl(b"x" * 20, source_format="x", source_label="y", imported_at="z", max_bytes=10)
        with self.assertRaises(AnnotationImportError):
            import_annotation_jsonl(b"", source_format="x", source_label="y", imported_at="z", max_bytes=0)
        with self.assertRaises(AnnotationImportError):
            import_annotation_jsonl(b"", source_format="x", source_label="y", imported_at="z", max_bytes="bad")


if __name__ == "__main__":
    unittest.main()
