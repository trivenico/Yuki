import json
import tempfile
import unittest
from pathlib import Path

from yuki_hf_model_diff import SnapshotError, compare_snapshots, main, render_markdown


def model(model_id, *, license="mit", task="text-generation", modified="2026-01-01T00:00:00Z", tags=None):
    return {"model_id":model_id,"url":"https://huggingface.co/"+model_id,"task":task,
            "license":license,"license_known":bool(license),"last_modified":modified,
            "tags":tags or [],"review_flags":[],"automatic_activation":False}


def write(path, models, generated):
    path.write_text(json.dumps({"schema_version":1,"generated_at":generated,"models":models}), encoding="utf-8")


class ModelSnapshotDiffTests(unittest.TestCase):
    def test_detects_new_changed_disappeared_and_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev, curr = Path(tmp)/"prev.json", Path(tmp)/"curr.json"
            write(prev,[model("org/keep"),model("org/change"),model("org/missing")],"old")
            write(curr,[model("org/keep"),model("org/change",license="apache-2.0"),model("org/new")],"new")
            report=compare_snapshots(prev,curr)
            self.assertEqual(report["summary"], {"previous_count":3,"current_count":3,"new_count":1,
                "changed_count":1,"disappeared_count":1,"unchanged_count":1})
            self.assertEqual(report["new_models"][0]["model_id"],"org/new")
            self.assertIn("license_metadata_changed_recheck_before_use",report["changed_models"][0]["review_flags"])
            self.assertEqual(report["disappeared_model_ids"],["org/missing"])
            self.assertFalse(report["policy"]["automatic_activation"])

    def test_change_fingerprint_covers_task_tags_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev,curr=Path(tmp)/"p.json",Path(tmp)/"c.json"
            write(prev,[model("org/a")],"old")
            write(curr,[model("org/a",task="feature-extraction",tags=["new"],modified="2026-02-01T00:00:00Z")],"new")
            fields={x["field"] for x in compare_snapshots(prev,curr)["changed_models"][0]["changed_fields"]}
            self.assertEqual(fields,{"task","tags","last_modified"})

    def test_rejects_duplicate_ids_bad_schema_and_oversized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev,curr=Path(tmp)/"p.json",Path(tmp)/"c.json"
            write(prev,[model("org/a"),model("org/a")],"old")
            write(curr,[model("org/a")],"new")
            with self.assertRaises(SnapshotError): compare_snapshots(prev,curr)
            prev.write_text(json.dumps({"schema_version":99,"models":[]} ),encoding="utf-8")
            with self.assertRaises(SnapshotError): compare_snapshots(prev,curr)
            prev.write_text(" "*(10_000_001),encoding="utf-8")
            with self.assertRaises(SnapshotError): compare_snapshots(prev,curr)

    def test_markdown_is_human_review_oriented(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev,curr=Path(tmp)/"p.json",Path(tmp)/"c.json"
            write(prev,[],"old"); write(curr,[model("org/new")],"new")
            md=render_markdown(compare_snapshots(prev,curr))
            self.assertIn("human review required",md)
            self.assertIn("not an exhaustive release feed",md.lower())

    def test_cli_writes_json_and_markdown_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev,curr=Path(tmp)/"p.json",Path(tmp)/"c.json"
            write(prev,[],"old"); write(curr,[model("org/new")],"new")
            output=Path(tmp)/"out"
            self.assertEqual(main(["--previous",str(prev),"--current",str(curr),"--output-dir",str(output)]),0)
            self.assertEqual(len(list(output.glob("*.json"))),1)
            self.assertEqual(len(list(output.glob("*.md"))),1)


if __name__ == "__main__":
    unittest.main()
