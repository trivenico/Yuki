"""Explicit real-inference smoke test for the configured local Ollama provider.

Run from the repository root with:
    python scripts/smoke_test_ollama.py

This script never falls back to a mock or cloud provider and does not change config.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Support direct execution on Windows without requiring an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config
from providers.factory import create_provider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Installed Ollama model ID; overrides config for this run only.")
    parser.add_argument("--base-url", help="Ollama endpoint; must be HTTPS or loopback HTTP.")
    args = parser.parse_args(argv)
    try:
        config = load_config()
    except Exception as exc:
        print(f"FAIL: Yuki configuration could not be loaded ({type(exc).__name__}).")
        return 1

    if args.model:
        config["ollama_model"] = args.model
    if args.base_url:
        config["ollama_base_url"] = args.base_url

    provider_name = str(config.get("provider", "")).strip().lower()
    if provider_name != "ollama":
        print("FAIL: configured provider is not Ollama.")
        print("Set provider: ollama in your Yuki config, or run with YUKI_PROVIDER=ollama.")
        print("No request was sent to any provider.")
        return 2

    model = str(config.get("ollama_model", "")).strip()
    if not model:
        print("FAIL: no Ollama model is configured.")
        print("Set ollama_model to an installed model name, for example llama3.2:3b.")
        print("No request was sent.")
        return 2

    try:
        provider = create_provider(config)
    except Exception as exc:
        print(f"FAIL: Ollama provider configuration is invalid ({type(exc).__name__}).")
        return 3

    print(f"Checking Ollama endpoint for model: {model}")
    if not provider.is_available():
        print("FAIL: Ollama is unreachable or the configured model is not installed.")
        print("Start Ollama, run 'ollama list', and verify ollama_base_url and ollama_model.")
        print("No cloud or mock fallback was attempted.")
        return 4

    try:
        response = provider.generate(
            "Reply with exactly this text and nothing else: YUKI_OLLAMA_SMOKE_OK"
        )
    except Exception as exc:
        print(f"FAIL: real Ollama inference failed ({type(exc).__name__}).")
        print("Check that Ollama is running, the model is loaded, and enough memory is available.")
        return 5

    if not isinstance(response, str) or not response.strip():
        print("FAIL: Ollama returned an empty response.")
        return 6

    print("PASS: real Ollama inference returned a non-empty response.")
    print("Response preview:")
    print(response.strip()[:500])
    print("This confirms provider connectivity and inference only; it is not a full application acceptance test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
