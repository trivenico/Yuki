import hashlib
import tempfile
import unittest
from pathlib import Path

from providers.dataset_records import (
    DatasetRecordError,
    deduplicate_dataset_records,
    deterministic_dataset_split,
    parse_dataset_records,
)


class DatasetRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_parses_jsonl_to_canonical_records_and_digest(self):
        path = self.root / "data.jsonl"
        path.write_text('{"prompt":" Hello ","response":" World "}\n', encoding="utf-8")
        result = parse_dataset_records(path)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["prompt"], "Hello")
        self.assertEqual(result["canonical_sha256"],
                         hashlib.sha256(result["canonical_jsonl"].encode()).hexdigest())

    def test_parses_csv(self):
        path = self.root / "data.csv"
        path.write_text('prompt,response\n"hello","world"\n', encoding="utf-8")
        result = parse_dataset_records(path)
        self.assertEqual(result["records"][0]["response"], "world")

    def test_rejects_malformed_or_unknown_records(self):
        for name, raw in (("bad.jsonl", '{"prompt":'), ("extra.jsonl",
                '{"prompt":"x","response":"y","exec":"bad"}')):
            path = self.root / name
            path.write_text(raw, encoding="utf-8")
            with self.subTest(name=name), self.assertRaises(DatasetRecordError):
                parse_dataset_records(path)

    def test_enforces_file_record_and_count_limits(self):
        path = self.root / "large.jsonl"
        path.write_text('{"prompt":"hello","response":"world"}\n', encoding="utf-8")
        with self.assertRaisesRegex(DatasetRecordError, "dataset_file_size_limit_exceeded"):
            parse_dataset_records(path, max_file_bytes=2)
        with self.assertRaisesRegex(DatasetRecordError, "record_size_limit_exceeded"):
            parse_dataset_records(path, max_record_bytes=2)
        with self.assertRaisesRegex(DatasetRecordError, "record_count_limit_exceeded"):
            parse_dataset_records(path, max_records=0)

    def test_deduplicates_stably(self):
        records = [{"prompt":"x","response":"y"}, {"prompt":"x","response":"y"}]
        result = deduplicate_dataset_records(records)
        self.assertEqual(result["unique_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)

    def test_split_is_reproducible_and_order_independent(self):
        records = deduplicate_dataset_records([
            {"prompt":f"prompt-{i}","response":f"response-{i}"} for i in range(80)
        ])["records"]
        first = deterministic_dataset_split(records, seed="experiment-7")
        second = deterministic_dataset_split(list(reversed(records)), seed="experiment-7")
        self.assertEqual(first["split_manifest_sha256"], second["split_manifest_sha256"])
        self.assertEqual(first["splits"], second["splits"])

    def test_rejects_missing_digests_and_invalid_ratios(self):
        with self.assertRaisesRegex(DatasetRecordError, "record_content_digest_required"):
            deterministic_dataset_split([{"prompt":"x","response":"y"}])
        records = deduplicate_dataset_records([{"prompt":"x","response":"y"}])["records"]
        with self.assertRaisesRegex(DatasetRecordError, "split_ratios"):
            deterministic_dataset_split(records, train=0.8, validation=0.1, test=0.2)


if __name__ == "__main__":
    unittest.main()
