"""Inspect installed local Ollama models without downloading or modifying them."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.local_model_registry import LocalModelRegistry, ModelRegistryError
from core.readiness import _total_memory_bytes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("YUKI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        registry = LocalModelRegistry(args.base_url)
        models = registry.discover()
    except (ValueError, ModelRegistryError) as exc:
        print(f"Local model discovery unavailable: {exc}", file=sys.stderr)
        return 2

    memory_bytes = _total_memory_bytes()
    memory_gib = memory_bytes / (1024 ** 3) if memory_bytes else None
    recommendations = registry.recommend(models, available_memory_gib=memory_gib)
    output = {
        "inventory_source": "local-ollama-tags-api",
        "read_only": True,
        "available_memory_gib": round(memory_gib, 1) if memory_gib is not None else None,
        "models": registry.to_dict(models),
        "recommendations": [
            {
                "model_name": item.model_name,
                "estimated_memory_gib": item.estimated_memory_gib,
                "status": item.status,
                "role_fit": item.role_fit,
                "rationale": item.rationale,
            }
            for item in recommendations
        ],
        "notice": "Memory estimates are heuristics. License and role suitability remain unverified; no model was downloaded or executed.",
    }
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print("Yuki local model inventory (read-only)")
        if not models:
            print("No installed Ollama models were reported.")
        for item in recommendations:
            print(f"[{item.status}] {item.model_name}: {item.rationale}")
            print("  License: unknown; role suitability: unverified")
        print(output["notice"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
