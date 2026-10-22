"""Run one explicitly authorized, minimal inference request against configured Yuki provider.

Usage: python -m tools.provider_smoke_test --confirm-live-request
This command may transmit the prompt to the configured provider. Never pass secrets.
"""
from __future__ import annotations
import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import Callable

@dataclass(frozen=True)
class SmokeReport:
    provider: str
    status: str
    elapsed_seconds: float | None
    response_characters: int
    detail: str
    def to_dict(self):
        return asdict(self)

def run_smoke_test(*, config_loader: Callable, provider_factory: Callable, prompt: str = "Reply with exactly: YUKI_SMOKE_TEST_OK", clock: Callable = time.monotonic) -> SmokeReport:
    """Perform a single real generation; response content is never logged."""
    started = clock()
    provider_name = "unknown"
    try:
        config = config_loader()
        provider_name = str(config.get("provider", "unknown")).strip().lower()
        if provider_name == "mock":
            return SmokeReport(provider_name, "fail", None, 0, "Mock provider is not a real language model.")
        provider = provider_factory(config)
        if not callable(getattr(provider, "is_available", None)) or not callable(getattr(provider, "generate", None)):
            return SmokeReport(provider_name, "fail", None, 0, "Configured provider does not satisfy the provider contract.")
        if provider.is_available() is not True:
            return SmokeReport(provider_name, "fail", None, 0, "Provider reports unavailable; no prompt was sent.")
        response = provider.generate(prompt)
        elapsed = max(0.0, clock() - started)
        if not isinstance(response, str) or not response.strip():
            return SmokeReport(provider_name, "fail", elapsed, 0, "Provider returned an empty or unsupported response.")
        return SmokeReport(provider_name, "pass", elapsed, len(response), "Real inference returned non-empty text; response content omitted from report.")
    except Exception as exc:
        elapsed = max(0.0, clock() - started)
        return SmokeReport(provider_name, "fail", elapsed, 0, f"Smoke test failed ({type(exc).__name__}); details withheld.")

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live-request", action="store_true", help="confirm that one prompt may be sent to the configured model/provider")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    if not args.confirm_live_request:
        parser.error("refusing to contact a provider without --confirm-live-request")
    from core.config import load_config
    from providers.factory import create_provider
    report = run_smoke_test(config_loader=load_config, provider_factory=lambda config: create_provider(config))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"Provider smoke test: {report.status.upper()} ({report.provider})")
        if report.elapsed_seconds is not None:
            print(f"Elapsed seconds: {report.elapsed_seconds:.2f}; response characters: {report.response_characters}")
        print(report.detail)
    return 0 if report.status == "pass" else 1

if __name__ == "__main__":
    raise SystemExit(main())
