"""Versioned local benchmark dataset loading and reproducible report assembly."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evaluation.model_eval import EvalCase, ModelEvaluator


def load_benchmark(path, *, max_cases=50, max_prompt_chars=8000):
    """Load a local UTF-8 benchmark and fingerprint its exact source bytes."""
    source = Path(path)
    raw = source.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("benchmark file exceeds 1 MB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("benchmark must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("benchmark must be a JSON object")
    name, version, cases = payload.get("name"), payload.get("version"), payload.get("cases")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("benchmark name must be a non-empty string up to 120 characters")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("benchmark version must be a positive integer")
    if not isinstance(cases, list) or not cases or len(cases) > max_cases:
        raise ValueError(f"benchmark must contain 1 to {max_cases} cases")
    converted, seen = [], set()
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError("each benchmark case must be an object")
        case_id, prompt, terms = item.get("id"), item.get("prompt"), item.get("expected_terms", [])
        if not isinstance(case_id, str) or not case_id.strip() or len(case_id) > 100:
            raise ValueError("each benchmark case needs a non-empty id up to 100 characters")
        if case_id in seen:
            raise ValueError("benchmark case IDs must be unique")
        seen.add(case_id)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > max_prompt_chars:
            raise ValueError("benchmark prompts must be non-empty and within the prompt limit")
        if not isinstance(terms, list) or len(terms) > 20 or any(
            not isinstance(term, str) or not term.strip() or len(term) > 120 for term in terms
        ):
            raise ValueError("expected_terms must contain at most 20 short strings")
        converted.append(EvalCase(
            case_id=case_id, task=str(item.get("task", "general"))[:64],
            prompt=prompt, required_terms=tuple(terms),
            max_latency_seconds=float(item.get("max_latency_seconds", 120)),
            max_response_chars=int(item.get("max_response_chars", 20000)),
        ))
    return {
        "name": name.strip(),
        "version": version,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cases": tuple(converted),
    }


def run_local_benchmark(provider, model_id, benchmark_path, *, minimum_pass_rate=0.8):
    """Run the versioned suite against an explicitly supplied local provider."""
    if not callable(getattr(provider, "generate", None)):
        raise TypeError("provider must expose generate(prompt)")
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
        raise ValueError("model_id must be a non-empty identifier up to 200 characters")
    if not provider.is_available():
        raise RuntimeError("configured local model is unavailable or not installed")
    dataset = load_benchmark(benchmark_path)
    evaluator = ModelEvaluator(
        max_cases=len(dataset["cases"]),
        max_prompt_chars=8000,
        max_total_duration_seconds=1800,
        minimum_pass_rate=minimum_pass_rate,
    )
    report = evaluator.run(model_id, provider.generate, dataset["cases"], approval_required=True)
    return {
        "schema_version": 1,
        "candidate_id": model_id,
        "inference_backend": "ollama_local",
        "benchmark": {
            "name": dataset["name"],
            "version": dataset["version"],
            "sha256": dataset["sha256"],
            "case_count": len(dataset["cases"]),
        },
        "evaluation": report.to_dict(),
        "promotion_policy": "human_review_required",
        "automatic_activation": False,
        "limitations": [
            "Expected and forbidden term checks are narrow proxies, not semantic quality measures.",
            "Synchronous provider calls cannot be forcibly interrupted by the suite deadline; provider request timeouts must be configured.",
            "A benchmark report is not a safety, license, factuality, or general-intelligence certification.",
        ],
    }
