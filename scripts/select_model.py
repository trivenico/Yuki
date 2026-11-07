"""Recommend a local Ollama model using reviewed capability and benchmark evidence."""
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
from core.model_capabilities import ModelCapability, ModelCapabilityRegistry
from core.model_selection import BenchmarkEvidence, select_local_models
from core.readiness import _total_memory_bytes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("reasoning", "coding", "vision", "embeddings", "transcription", "speech_generation", "tool_use"))
    parser.add_argument("--profiles", required=True, help="Operator-reviewed JSON evidence file.")
    parser.add_argument("--base-url", default=os.environ.get("YUKI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--minimum-score", type=float, default=0.70)
    args = parser.parse_args(argv)

    try:
        payload = json.loads(Path(args.profiles).expanduser().read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"capabilities", "benchmarks"}:
            raise ValueError("profiles JSON must contain capabilities and benchmarks arrays.")
        if not isinstance(payload["capabilities"], list) or not isinstance(payload["benchmarks"], list):
            raise ValueError("capabilities and benchmarks must be arrays.")
        capabilities = ModelCapabilityRegistry(
            ModelCapability(
                item["model_id"], frozenset(item["capabilities"]), item["evidence"],
                item.get("verified", False), item.get("runtime_status", "unknown"),
                item.get("model_digest"),
            )
            for item in payload["capabilities"]
        )
        benchmarks = [BenchmarkEvidence(**item) for item in payload["benchmarks"]]
        models = LocalModelRegistry(args.base_url).discover()
        memory_bytes = _total_memory_bytes()
        memory_gib = memory_bytes / (1024 ** 3) if memory_bytes else None
        candidates = select_local_models(
            args.role, models, capabilities, benchmarks,
            available_memory_gib=memory_gib, minimum_score=args.minimum_score,
        )
    except (OSError, KeyError, TypeError, ValueError, ModelRegistryError) as exc:
        print(f"Model selection unavailable: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "role": args.role,
        "activation": "manual approval required",
        "automatic_activation": False,
        "candidates": [item.__dict__ for item in candidates],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
