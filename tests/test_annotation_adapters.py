import json
import unittest

from knowledge.annotation_adapters import (
    AnnotationAdapterError,
    import_cvat_xml_export,
    import_doccano_export,
    import_label_studio_export,
    import_via_json_export,
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
        boolean_geometry = [{"id": 2, "annotations": [{"result": [{
            "type": "rectanglelabels",
            "value": {"x": True, "y": 0, "width": 20, "height": 10, "rectanglelabels": ["x"]},
        }]}]}]
        with self.assertRaises(AnnotationAdapterError):
            import_label_studio_export(
                json.dumps(boolean_geometry).encode(), source_label="test", imported_at="now",
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

    def test_imports_cvat_image_boxes_and_polygons(self):
        xml = b"""<annotations><image id="1" name="sample.jpg" width="100" height="50">
        <box label="vehicle" xtl="10" ytl="5" xbr="40" ybr="25" />
        <polygon label="zone" points="0,0;100,0;50,50" />
        <tag label="ignored" />
        </image></annotations>"""
        dataset = import_cvat_xml_export(
            xml, source_label="cvat-export", imported_at="2026-11-20T10:00:00Z",
        )
        self.assertEqual([item.task_type for item in dataset.annotations], ["bbox", "polygon"])
        self.assertEqual(dataset.annotations[0].geometry, (0.1, 0.1, 0.3, 0.4))
        self.assertEqual(dataset.skipped_records, 1)
        self.assertEqual(dataset.evidence[0].source_format, "cvat-xml")

    def test_rejects_cvat_dtd_and_invalid_xml(self):
        with self.assertRaises(AnnotationAdapterError):
            import_cvat_xml_export(
                b'<!DOCTYPE annotations [<!ENTITY x "boom">]><annotations />',
                source_label="test", imported_at="now",
            )
        with self.assertRaises(AnnotationAdapterError):
            import_cvat_xml_export(b"<broken", source_label="test", imported_at="now")

    def test_imports_via_rectangles_and_polygons(self):
        export = {
            "_via_img_metadata": {
                "img1": {
                    "filename": "image.png", "width": 200, "height": 100,
                    "regions": [
                        {"shape_attributes": {"name": "rect", "x": 20, "y": 10, "width": 40, "height": 30},
                         "region_attributes": {"class": "object"}},
                        {"shape_attributes": {"name": "polygon", "all_points_x": [0, 200, 100], "all_points_y": [0, 0, 100]},
                         "region_attributes": {"class": "area"}},
                    ],
                },
            },
        }
        dataset = import_via_json_export(
            json.dumps(export).encode(), source_label="via-export",
            imported_at="2026-11-20T10:00:00Z",
        )
        self.assertEqual([item.task_type for item in dataset.annotations], ["bbox", "polygon"])
        self.assertEqual(dataset.annotations[0].geometry, (0.1, 0.1, 0.2, 0.3))
        self.assertEqual(dataset.evidence[0].source_format, "via-v2-json")

    def test_rejects_via_invalid_geometry_and_duplicate_keys(self):
        bad = {"_via_img_metadata": {"img": {
            "filename": "a.png", "width": 10, "height": 10,
            "regions": [{"shape_attributes": {"name": "rect", "x": 9, "y": 0, "width": 2, "height": 2},
                         "region_attributes": {"class": "x"}}],
        }}}
        with self.assertRaises(AnnotationAdapterError):
            import_via_json_export(json.dumps(bad).encode(), source_label="test", imported_at="now")
        with self.assertRaises(AnnotationAdapterError):
            import_via_json_export(b'{"_via_img_metadata":{},"_via_img_metadata":{}}', source_label="test", imported_at="now")

    def test_enforces_input_limits(self):
        with self.assertRaises(AnnotationAdapterError):
            import_doccano_export(b"x" * 20, source_label="test", imported_at="now", max_bytes=10)
        with self.assertRaises(AnnotationAdapterError):
            import_label_studio_export(b"[]", source_label="test", imported_at="now", max_bytes=0)


if __name__ == "__main__":
    unittest.main()
