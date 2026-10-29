"""Explicit real-inference smoke test for the configured local Ollama provider.

Run from the repository root with:
    python scripts/smoke_test_ollama.py
    python scripts/smoke_test_ollama.py --model llama3.2:3b
    python scripts/smoke_test_ollama.py --base-url http://127.0.0.1:11434 --timeout 60

Overrides apply to this invocation only. The script never falls back to a mock or
cloud provider and does not change the saved Yuki configuration.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Support direct execution from a source checkout without an editable install.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import load_config  # noqa: E402
from providers.factory import create_provider  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the configured local Ollama endpoint and perform real inference."
    )
    parser.add_argument(
        "--model",
        help="Installed Ollama model for this check (default: configured Yuki model).",
    )
    parser.add_argument(
        "--base-url",
        help="Ollama endpoint for this check (default: configured Yuki endpoint).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Request timeout in seconds (1-120; default: YUKI_PROVIDER_TIMEOUT_SECONDS or 45).",
    )
    args = parser.parse_args(argv)
    if args.timeout is not None and not 1 <= args.timeout <= 120:
        parser.error("argument --timeout: timeout must be between 1 and 120 seconds")

    try:
        config = load_config()
    except Exception as exc:
        print(f"FAIL: Yuki configuration could not be loaded ({type(exc).__name__}).")
        return 1

    # Match the runtime's provider-selection precedence, but refuse any non-local
    # selection before constructing a provider so this script can never probe cloud.
    provider_name = str(os.environ.get("YUKI_PROVIDER", config.get("provider", "ollama"))).strip().lower()
    if provider_name != "ollama":
        print("FAIL: the effective provider selection is not Ollama.")
        print("Select Ollama in Yuki config or set YUKI_PROVIDER=ollama for this invocation.")
        print("No request was sent to any provider.")
        return 2

    model = str(
        args.model if args.model is not None
        else os.environ.get("YUKI_OLLAMA_MODEL", config.get("ollama_model", ""))
    ).strip()
    base_url = str(
        args.base_url if args.base_url is not None
        else os.environ.get("YUKI_OLLAMA_BASE_URL", config.get("ollama_base_url", ""))
    ).strip()
    if not model:
        print("FAIL: no Ollama model is configured.")
        print("Set ollama_model to an installed model name, for example llama3.2:3b.")
        print("No request was sent.")
        return 2
    if not base_url:
        print("FAIL: no Ollama endpoint is configured.")
        print("Set ollama_base_url or pass --base-url.")
        print("No request was sent.")
        return 2

    provider_env = dict(os.environ)
    # Pin the provider for this invocation after the effective-selection guard above.
    provider_env["YUKI_PROVIDER"] = "ollama"
    provider_env["YUKI_OLLAMA_MODEL"] = model
    provider_env["YUKI_OLLAMA_BASE_URL"] = base_url
    if args.timeout is not None:
        provider_env["YUKI_PROVIDER_TIMEOUT_SECONDS"] = format(args.timeout, "g")
    else:
        raw_timeout = provider_env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", "45")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            parser.error(f"argument --timeout: invalid float value: {raw_timeout!r}")
        if not 1 <= timeout <= 120:
            parser.error("argument --timeout: timeout must be between 1 and 120 seconds")

    config = dict(config)
    config["ollama_model"] = model
    config["ollama_base_url"] = base_url
    try:
        provider = create_provider(config, environ=provider_env)
    except Exception as exc:
        print(f"FAIL: Ollama provider configuration is invalid ({type(exc).__name__}).")
        return 3

    print(f"Checking local Ollama endpoint for model: {model}")
    try:
        available = provider.is_available()
    except Exception as exc:
        print(f"FAIL: Ollama availability check failed ({type(exc).__name__}).")
        print("No cloud or mock fallback was attempted.")
        return 4
    if not available:
        print("FAIL: Ollama is unreachable or the configured model is not installed.")
        print("Start Ollama, run 'ollama list', and verify the endpoint and model.")
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
    if "YUKI_OLLAMA_SMOKE_OK" not in response:
        print("FAIL: Ollama responded, but the expected smoke-test sentinel was absent.")
        print("The model may have ignored the instruction; no fallback was attempted.")
        return 7

    print("PASS: real Ollama inference returned the expected smoke-test sentinel.")
    print("Response preview:")
    print(response.strip()[:500])
    print("This confirms provider connectivity and inference only; it is not a full application acceptance test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
