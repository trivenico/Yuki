"""Record an existing evaluation report and rank reviewed model candidates."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from providers.model_registry import (
    RegistryError, load_registry, record_evaluation, recommend, save_registry, validate_evaluation,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record model evaluation evidence and generate review-first recommendations.")
    parser.add_argument("--report", required=True, help="Existing JSON report from yuki_evaluate_model.py")
    parser.add_argument("--benchmark-id", required=True, help="Stable benchmark/version ID, e.g. starter-v1")
    parser.add_argument("--registry", default=".yuki/model_registry.json", help="Local registry JSON path")
    parser.add_argument("--license-status", choices=["verified","restricted","unknown","not_applicable"], default="unknown")
    parser.add_argument("--runtime-status", choices=["verified","incompatible","unknown"], default="unknown")
    parser.add_argument("--cost-per-1k-tokens", type=float, help="Optional independently verified cost estimate")
    parser.add_argument("--recommend", action="store_true", help="Print recommendations for this benchmark after recording")
    args = parser.parse_args(argv)
    try:
        with open(args.report, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        record = validate_evaluation(report, benchmark_id=args.benchmark_id,
                                     license_status=args.license_status,
                                     runtime_status=args.runtime_status,
                                     cost_per_1k_tokens=args.cost_per_1k_tokens)
        registry_path = Path(args.registry)
        registry = load_registry(registry_path)
        record_evaluation(registry, record)
        save_registry(registry_path, registry)
        result = {"recorded": True, "registry": str(registry_path), "record": record}
        if args.recommend:
            result["recommendations"] = recommend(registry, benchmark_id=args.benchmark_id)
    except (OSError, json.JSONDecodeError, ValueError, RegistryError) as exc:
        print(f"model registry operation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
