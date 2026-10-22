"""Compare Hugging Face scout snapshots and report model metadata changes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_MAX_INPUT_BYTES = 10_000_000
_MAX_MODELS = 10_000
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_FIELDS = ("task", "license", "license_known", "last_modified", "tags", "review_flags")


class SnapshotError(ValueError):
    """Snapshot is malformed or exceeds safe bounds."""


def _load_snapshot(path):
    path = Path(path)
    try:
        if path.stat().st_size > _MAX_INPUT_BYTES:
            raise SnapshotError("snapshot exceeds size limit")
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("could not read valid snapshot JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SnapshotError("unsupported snapshot schema")
    models = data.get("models")
    if not isinstance(models, list) or len(models) > _MAX_MODELS:
        raise SnapshotError("snapshot model list is invalid or too large")
    normalized = {}
    for model in models:
        if not isinstance(model, dict):
            raise SnapshotError("snapshot contains an invalid model record")
        model_id = model.get("model_id")
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise SnapshotError("snapshot contains an invalid model ID")
        if model_id in normalized:
            raise SnapshotError("snapshot contains duplicate model IDs")
        normalized[model_id] = model
    return data, normalized


def _fingerprint(model):
    payload = {field: model.get(field) for field in _FIELDS}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compare_snapshots(previous_path, current_path):
    previous_doc, previous = _load_snapshot(previous_path)
    current_doc, current = _load_snapshot(current_path)
    previous_ids, current_ids = set(previous), set(current)
    new_ids = sorted(current_ids - previous_ids)
    missing_ids = sorted(previous_ids - current_ids)
    changed = []
    unchanged_count = 0
    for model_id in sorted(previous_ids & current_ids):
        before, after = previous[model_id], current[model_id]
        before_fp, after_fp = _fingerprint(before), _fingerprint(after)
        if before_fp == after_fp:
            unchanged_count += 1
            continue
        changed_fields = [
            {"field": field, "before": before.get(field), "after": after.get(field)}
            for field in _FIELDS if before.get(field) != after.get(field)
        ]
        flags = []
        if any(item["field"] == "license" or item["field"] == "license_known" for item in changed_fields):
            flags.append("license_metadata_changed_recheck_before_use")
        if any(item["field"] == "task" for item in changed_fields):
            flags.append("task_metadata_changed_recheck_compatibility")
        if any(item["field"] == "last_modified" for item in changed_fields):
            flags.append("upstream_modified_timestamp_changed")
        changed.append({"model_id": model_id, "changed_fields": changed_fields, "review_flags": flags})
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "yuki_hf_snapshot_diff",
        "previous_generated_at": previous_doc.get("generated_at"),
        "current_generated_at": current_doc.get("generated_at"),
        "summary": {
            "previous_count": len(previous),
            "current_count": len(current),
            "new_count": len(new_ids),
            "changed_count": len(changed),
            "disappeared_count": len(missing_ids),
            "unchanged_count": unchanged_count,
        },
        "new_models": [{"model_id": model_id, "metadata": current[model_id],
                        "review_flags": ["new_candidate_requires_model_card_license_and_security_review"]}
                       for model_id in new_ids],
        "changed_models": changed,
        "disappeared_model_ids": missing_ids,
        "policy": {
            "automatic_activation": False,
            "automatic_download": False,
            "meaning": "Metadata differences are triage signals, not proof of quality, safety, license or compatibility.",
            "disappeared": "Absence from a bounded search is not proof that a model was deleted.",
        },
    }


def render_markdown(report):
    summary = report["summary"]
    lines = [
        "# Yuki Hugging Face model change report", "",
        f"Generated: {report['generated_at']}", "",
        f"- New candidates: **{summary['new_count']}**",
        f"- Changed metadata: **{summary['changed_count']}**",
        f"- Not present in current bounded snapshot: **{summary['disappeared_count']}**",
        f"- Unchanged: **{summary['unchanged_count']}**", "",
        "## New candidates", "",
    ]
    if not report["new_models"]:
        lines.append("No new model IDs in this snapshot.")
    else:
        for item in report["new_models"]:
            model = item["metadata"]
            lines.append(f"- [{item['model_id']}](https://huggingface.co/{item['model_id']}) — "
                         f"task: {model.get('task') or 'unknown'}; "
                         f"license: {model.get('license') or 'unknown'}; human review required.")
    lines.extend(["", "## Changed metadata", ""])
    if not report["changed_models"]:
        lines.append("No metadata changes for models present in both snapshots.")
    else:
        for item in report["changed_models"]:
            fields = ", ".join(field["field"] for field in item["changed_fields"])
            lines.append(f"- [{item['model_id']}](https://huggingface.co/{item['model_id']}) — changed: {fields}.")
    lines.extend([
        "", "## Interpretation", "",
        "This is a comparison of two bounded public-metadata snapshots, not an exhaustive release feed. "
        "Missing models may simply have fallen outside the current result window. Review official model cards, "
        "licenses, provenance, runtime compatibility and security before evaluating candidates. "
        "Nothing is downloaded or activated automatically.", "",
    ])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare two Yuki Hugging Face scout JSON snapshots.")
    parser.add_argument("--previous", required=True, help="Earlier scout JSON report")
    parser.add_argument("--current", required=True, help="Newer scout JSON report")
    parser.add_argument("--output-dir", default="artifacts/model-scout-diffs")
    args = parser.parse_args(argv)
    try:
        report = compare_snapshots(args.previous, args.current)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path = output_dir / f"hf-model-diff-{stamp}.json"
        markdown_path = output_dir / f"hf-model-diff-{stamp}.md"
        with json_path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        with markdown_path.open("x", encoding="utf-8") as handle:
            handle.write(render_markdown(report))
    except (SnapshotError, OSError, ValueError) as exc:
        print(f"model snapshot comparison failed: {exc}", file=sys.stderr)
        return 2
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
