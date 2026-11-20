import json
import unittest

from knowledge.annotation import import_annotation_jsonl
from knowledge.dataset_manifest import DatasetManifestError, create_dataset_manifest


class DatasetManifestTests(unittest.TestCase):
    def dataset(self):
        rows = [
            {"annotation_id": "a1", "task_type": "classification", "label": "cat", "asset_id": "img-1"},
            {"annotation_id": "a2", "task_type": "classification", "label": "dog", "asset_id": "img-2"},
            {"annotation_id": "a3", "task_type": "classification", "label": "bird", "asset_id": "img-3"},
        ]
        payload = b"\n".join(json.dumps(row).encode() for row in rows)
        return import_annotation_jsonl(
            payload, source_format="normalized", source_label="test",
            imported_at="2026-11-20T10:00:00Z",
        )

    def test_creates_deterministic_manifest_and_counts(self):
        dataset = self.dataset()
        assignments = {"img-1": "train", "img-2": "validation", "img-3": "test"}
        first = create_dataset_manifest(dataset, dataset_id="vision-v1", split_assignments=assignments)
        second = create_dataset_manifest(dataset, dataset_id="vision-v1", split_assignments=dict(reversed(list(assignments.items()))))
        self.assertEqual(first, second)
        self.assertEqual(first.split_counts, (("train", 1), ("validation", 1), ("test", 1)))
        self.assertFalse(first.verified)
        self.assertEqual(first.provenance, "owner_supplied_unverified")

    def test_rejects_group_leakage(self):
        with self.assertRaises(DatasetManifestError):
            create_dataset_manifest(
                self.dataset(), dataset_id="vision-v1",
                split_assignments={"img-1": "train", "img-2": "test", "img-3": "validation"},
                leakage_groups={"img-1": "same-source", "img-2": "same-source"},
            )

    def test_requires_exact_asset_coverage_and_valid_splits(self):
        with self.assertRaises(DatasetManifestError):
            create_dataset_manifest(self.dataset(), dataset_id="vision-v1", split_assignments={"img-1": "train"})
        with self.assertRaises(DatasetManifestError):
            create_dataset_manifest(
                self.dataset(), dataset_id="vision-v1",
                split_assignments={"img-1": "train", "img-2": "holdout", "img-3": "test"},
            )

    def test_rejects_unknown_groups_and_empty_datasets(self):
        dataset = self.dataset()
        with self.assertRaises(DatasetManifestError):
            create_dataset_manifest(
                dataset, dataset_id="vision-v1",
                split_assignments={"img-1": "train", "img-2": "validation", "img-3": "test"},
                leakage_groups={"not-an-asset": "group"},
            )
        empty = import_annotation_jsonl(b"", source_format="normalized", source_label="empty", imported_at="now")
        with self.assertRaises(DatasetManifestError):
            create_dataset_manifest(empty, dataset_id="empty", split_assignments={})


if __name__ == "__main__":
    unittest.main()
