"""End-to-end smoke test for Yuki's configured local Ollama provider.

Run from the repository root after starting Ollama and completing onboarding:
    python scripts/smoke_test_ollama.py
This sends one short prompt to the configured local model and prints its response.
It does not call tools or route the prompt to a cloud provider.
"""
from __future__ import annotations

import sys

from core.config import load_config
from core.onboarding import apply_user_config, load_user_config, onboarding_needed
from providers.factory import create_provider
from providers.ollama import OllamaProvider
from yuki_agent import YukiAgent


def main() -> int:
    if onboarding_needed():
        print("Yuki smoke test: configuration missing. Run: python -m ui.onboarding_tui", file=sys.stderr)
        return 2
    try:
        config = load_config()
        user_config = load_user_config()
        if user_config:
            config = apply_user_config(config, user_config)
        if str(config.get("provider", "")).lower() != "ollama":
            print("Yuki smoke test: this script requires provider=ollama. Select Ollama in onboarding first.", file=sys.stderr)
            return 2
        provider = create_provider(config)
        if not isinstance(provider, OllamaProvider):
            print("Yuki smoke test: configured provider did not resolve to Ollama.", file=sys.stderr)
            return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Yuki smoke test: configuration/provider initialization failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    print(f"Checking Ollama at {provider.base_url} for model {provider.model!r}...")
    if not provider.is_available():
        print(
            "Yuki smoke test: Ollama is unreachable or the configured model is not installed. "
            "Start Ollama, run ollama pull " + provider.model + ", and retry.",
            file=sys.stderr,
        )
        return 3
    try:
        answer = YukiAgent(provider).ask(
            "Reply with the exact text YUKI_OLLAMA_SMOKE_OK and no other words."
        )
    except Exception as exc:
        print(f"Yuki smoke test: real model request failed ({type(exc).__name__}). Check Ollama logs and retry.", file=sys.stderr)
        return 4
    print("Model response:")
    print(answer)
    if "YUKI_OLLAMA_SMOKE_OK" not in answer:
        print("Yuki smoke test: a response was received, but it did not match the expected marker.", file=sys.stderr)
        return 5
    print("PASS: YukiAgent completed a real local Ollama inference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
