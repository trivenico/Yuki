"""CLI for review-first Hugging Face model discovery.

Usage:
    python yuki_model_discovery.py --task text-generation --sort downloads --limit 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from providers.huggingface_discovery import DiscoveryError, HuggingFaceDiscovery


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Discover Hugging Face models without downloading or activating them.")
    parser.add_argument("--task", default="text-generation",
                        choices=["text-generation", "text2text-generation", "conversational",
                                 "feature-extraction", "image-text-to-text", "automatic-speech-recognition"])
    parser.add_argument("--sort", default="downloads", choices=["downloads", "likes", "lastModified"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--output", help="Optional JSON output path; defaults to stdout.")
    args = parser.parse_args(argv)
    try:
        result = HuggingFaceDiscovery(timeout=args.timeout, max_results=args.limit).discover(
            task=args.task, sort=args.sort, token=os.environ.get("HF_TOKEN")
        )
    except (ValueError, DiscoveryError) as exc:
        print(f"model discovery failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        # Explicit output path only; never overwrite an existing report silently.
        if os.path.exists(args.output):
            print("output file already exists; choose a new path", file=sys.stderr)
            return 2
        try:
            with open(args.output, "x", encoding="utf-8") as handle:
                handle.write(rendered)
        except OSError:
            print("could not write output report", file=sys.stderr)
            return 2
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
