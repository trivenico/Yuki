"""Scheduled read-only Hugging Face model scout.

Fetches public metadata only and writes a timestamped report. It never downloads
weights, executes remote code, installs dependencies or activates a model.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from providers.huggingface_discovery import DiscoveryError, HuggingFaceDiscovery

TASKS = (
    "text-generation",
    "text2text-generation",
    "conversational",
    "feature-extraction",
    "image-text-to-text",
    "automatic-speech-recognition",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scout recent public Hugging Face models for Yuki.")
    parser.add_argument("--limit-per-task", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--output-dir", default="artifacts/model-scout")
    args = parser.parse_args(argv)
    if isinstance(args.limit_per_task, bool) or not 1 <= args.limit_per_task <= 50:
        parser.error("--limit-per-task must be between 1 and 50")
    try:
        scout = HuggingFaceDiscovery(timeout=args.timeout, max_results=args.limit_per_task)
        reports = []
        for task in TASKS:
            reports.append(scout.discover(task=task, sort="lastModified"))
    except (ValueError, DiscoveryError) as exc:
        print(f"model scout failed: {exc}", file=sys.stderr)
        return 2

    seen = set()
    models = []
    for report in reports:
        for model in report["models"]:
            if model["model_id"] in seen:
                continue
            seen.add(model["model_id"])
            models.append(model)
    models.sort(key=lambda item: item.get("last_modified") or "", reverse=True)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "huggingface_hub_public_model_metadata",
        "scope": {"tasks": list(TASKS), "limit_per_task": args.limit_per_task},
        "candidate_count": len(models),
        "models": models,
        "review_policy": {
            "status": "needs_human_review",
            "automatic_activation": False,
            "downloads_weights": False,
            "executes_remote_code": False,
        },
        "limitations": [
            "This is a bounded metadata snapshot, not an exhaustive list of newly published models.",
            "last_modified is upstream metadata and does not prove a model is new, safe, compatible, or high quality.",
            "Model-card/license review and benchmark evaluation are required before adoption.",
        ],
    }
    out_dir = Path(args.output_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"hf-model-scout-{stamp}.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError:
        print("could not write scout report", file=sys.stderr)
        return 2

    print(f"Report: {path}")
    print(f"Unique candidates: {len(models)}")
    print("Newest reported model metadata:")
    for model in models[:10]:
        print(f"- {model['model_id']} | task={model.get('task') or 'unknown'} | "
              f"modified={model.get('last_modified') or 'unknown'} | "
              f"license={model.get('license') or 'unknown'} | review=required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
