"""Run a small explicit benchmark against a Hugging Face Inference Router model."""
from __future__ import annotations
import argparse
import json
import os
import sys
from providers.model_evaluation import EvaluationError, HuggingFaceChatClient, evaluate_model


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate a Hugging Face model on a local JSON benchmark.")
    parser.add_argument("--model", required=True, help="Exact organization/model identifier")
    parser.add_argument("--benchmark", required=True, help="Path to a local JSON benchmark file")
    parser.add_argument("--limit", type=int, default=5, help="Maximum cases to run (1-20)")
    parser.add_argument("--timeout", type=float, default=20, help="Per-request timeout (1-30 seconds)")
    parser.add_argument("--output", help="Optional new JSON report path (must not already exist)")
    args = parser.parse_args(argv)
    try:
        with open(args.benchmark, "r", encoding="utf-8") as handle:
            benchmark = json.load(handle)
        if not isinstance(benchmark, dict) or not isinstance(benchmark.get("cases"), list):
            raise ValueError("benchmark JSON must be an object with a cases list")
        token = os.environ.get("HF_TOKEN", "")
        client = HuggingFaceChatClient(token, timeout=args.timeout)
        report = evaluate_model(args.model, benchmark["cases"],
                                lambda prompt: client.generate(args.model, prompt),
                                max_cases=args.limit)
    except (OSError, json.JSONDecodeError, ValueError, EvaluationError) as exc:
        print(f"model evaluation failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        if os.path.exists(args.output):
            print("output file already exists; choose a new path", file=sys.stderr)
            return 2
        try:
            with open(args.output, "x", encoding="utf-8") as handle:
                handle.write(rendered)
        except OSError:
            print("could not write evaluation report", file=sys.stderr)
            return 2
    else:
        print(rendered, end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
