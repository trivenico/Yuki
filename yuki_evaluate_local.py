"""Run a reproducible benchmark against a local Ollama model."""
from __future__ import annotations

import argparse
import json
import os
import sys

from evaluation.local_benchmark import run_local_benchmark
from providers.ollama import OllamaProvider


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate a local Ollama model with a versioned Yuki benchmark.")
    parser.add_argument("--model", required=True, help="Exact installed Ollama model name")
    parser.add_argument("--benchmark", default="benchmarks/model_evaluation_starter.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--minimum-pass-rate", type=float, default=0.8)
    parser.add_argument("--output", help="New report file path; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        provider = OllamaProvider(base_url=args.base_url, model=args.model, timeout=args.timeout)
        report = run_local_benchmark(provider, args.model, args.benchmark,
                                     minimum_pass_rate=args.minimum_pass_rate)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(args.output, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(rendered)
        else:
            print(rendered, end="")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print("local model evaluation failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
