"""End-to-end smoke test for Yuki's configured local Ollama provider.

Run from the repository root after configuring config.yaml and starting Ollama:
    python scripts/smoke_test_ollama.py

This sends one short prompt to the configured local model. It never routes to a
cloud provider and does not invoke tools.
"""
from __future__ import annotations

import sys

from core.config import load_config
from providers.factory import create_provider
from providers.ollama import OllamaProvider
from yuki_agent import YukiAgent


def main() -> int:
    try:
        config = load_config()
        if str(config.get("provider", "")).lower() != "ollama":
            print(
                "Yuki smoke test: set provider: ollama in config.yaml (or YUKI_PROVIDER=ollama).",
                file=sys.stderr,
            )
            return 2
        provider = create_provider(config)
        if not isinstance(provider, OllamaProvider):
            print("Yuki smoke test: configured provider did not resolve to Ollama.", file=sys.stderr)
            return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            f"Yuki smoke test: configuration/provider initialization failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return 2

    print(f"Checking Ollama at {provider.base_url} for model {provider.model!r}...")
    if not provider.is_available():
        print(
            "Yuki smoke test: Ollama is unreachable or the configured model is not installed. "
            f"Start Ollama and run: ollama pull {provider.model}",
            file=sys.stderr,
        )
        return 3

    answer = YukiAgent(provider).ask(
        "Reply with the exact text YUKI_OLLAMA_SMOKE_OK and no other words."
    )
    print("Model response:")
    print(answer)
    if "YUKI_OLLAMA_SMOKE_OK" not in answer:
        print(
            "Yuki smoke test: a response was received, but it did not match the expected marker.",
            file=sys.stderr,
        )
        return 4
    print("PASS: YukiAgent completed a real local Ollama inference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
