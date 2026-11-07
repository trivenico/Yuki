"""Opt-in real-provider acceptance check for Yuki local alpha.

This sends one fixed, harmless prompt to the configured provider. It never
prints prompts, responses, API keys, environment values, or raw exceptions.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROMPT = "Reply with exactly: Yuki local provider acceptance passed."
MAX_RESPONSE_CHARS = 4_000


def run_acceptance(*, config_loader=None, provider_factory=None, environ=None):
    env = os.environ if environ is None else environ
    if env.get("YUKI_RUN_REAL_PROVIDER_ACCEPTANCE") != "1":
        return {
            "result": "skipped",
            "reason": "Set YUKI_RUN_REAL_PROVIDER_ACCEPTANCE=1 to authorize one real provider request.",
        }

    if config_loader is None:
        from core.config import load_config
        config_loader = load_config
    if provider_factory is None:
        from providers.factory import create_provider
        provider_factory = create_provider

    config = config_loader()
    provider_name = str(config.get("provider", "")).strip().lower()
    if provider_name not in {"openai", "ollama", "gemini", "hybrid"}:
        raise RuntimeError("Configured provider is not supported for real acceptance testing.")

    provider = provider_factory(config)
    if provider.is_available() is not True:
        raise RuntimeError("Configured provider is not available; no request was sent.")

    # HybridProvider.generate() intentionally routes general work locally.
    # No provider fallback is attempted if the selected route fails.
    response = provider.generate(PROMPT)
    if not isinstance(response, str) or not response.strip():
        raise RuntimeError("Provider returned an empty or invalid response.")
    if len(response) > MAX_RESPONSE_CHARS:
        raise RuntimeError("Provider response exceeded the acceptance-test size limit.")

    model = getattr(provider, "model", None)
    if not isinstance(model, str) or not model:
        model = str(config.get("ollama_model") or config.get("openai_model") or provider_name)
    return {
        "result": "passed",
        "provider": provider_name,
        "model": model[:120],
        "response_characters": len(response),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "credentials_or_content_logged": False,
    }


def main() -> int:
    try:
        result = run_acceptance()
    except Exception as exc:
        # Exception messages may contain endpoint details or local paths.
        print(json.dumps({
            "result": "failed",
            "error_type": type(exc).__name__,
            "details": "Inspect local provider configuration and provider logs; sensitive exception text is suppressed.",
        }, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["result"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
