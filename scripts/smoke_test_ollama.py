"""Opt-in end-to-end smoke test for a real Ollama server.

Run after installing/starting Ollama and pulling the configured model:
    python scripts/smoke_test_ollama.py
    python scripts/smoke_test_ollama.py --model llama3.2:3b

This intentionally makes a real local HTTP request and is not part of unit CI.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow direct execution as a script from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.ollama import OllamaProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check real Ollama connectivity and inference.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("YUKI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        help="Ollama URL (default: YUKI_OLLAMA_BASE_URL or loopback).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("YUKI_OLLAMA_MODEL", "llama3.2:3b"),
        help="Installed Ollama model (default: YUKI_OLLAMA_MODEL or llama3.2:3b).",
    )
    # Defer environment parsing until after argparse handles --help. A malformed
    # environment default must not prevent a user from discovering CLI usage.
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Request timeout in seconds (1-120; defaults to YUKI_PROVIDER_TIMEOUT_SECONDS or 45).",
    )
    args = parser.parse_args()
    if args.timeout is None:
        raw_timeout = os.environ.get("YUKI_PROVIDER_TIMEOUT_SECONDS", "45")
        try:
            args.timeout = float(raw_timeout)
        except (TypeError, ValueError):
            parser.error(f"argument --timeout: invalid float value: {raw_timeout!r}")

    try:
        provider = OllamaProvider(base_url=args.base_url, model=args.model, timeout=args.timeout)
    except (TypeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    print(f"Checking Ollama endpoint and installed model: {args.model}")
    if not provider.is_available():
        print(
            "Ollama is unavailable or the configured model is not installed. "
            f"Start Ollama, run 'ollama pull {args.model}', and retry.",
            file=sys.stderr,
        )
        return 1

    try:
        answer = provider.generate("Reply with exactly: YUKI_OLLAMA_SMOKE_OK")
    except (RuntimeError, ValueError) as exc:
        print(f"Real inference failed: {exc}", file=sys.stderr)
        return 1

    if "YUKI_OLLAMA_SMOKE_OK" not in answer:
        print("Inference returned text, but did not contain the expected marker.", file=sys.stderr)
        print(f"Received response length: {len(answer)} characters", file=sys.stderr)
        return 1

    print("PASS: Ollama is reachable, the model is installed, and real inference succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
