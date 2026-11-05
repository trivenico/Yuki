"""Explicit live conformance checks against Yuki's configured real provider.

No mock provider is accepted. The command sends a minimal prompt only after
operator confirmation; it never supplies tools or executes returned proposals.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from providers.agent_adapter import ProviderAgentAdapter
from providers.adapter_conformance import inspect_provider_adapter


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class LiveConformanceReport:
    schema_version: int
    provider: str
    verification_level: str
    checks: tuple[Check, ...]
    elapsed_seconds: float
    response_characters: int

    @property
    def status(self) -> str:
        statuses = {item.status for item in self.checks}
        if "fail" in statuses:
            return "fail"
        if "not_run" in statuses:
            return "partial"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status
        return result


def _safe_provider_name(config: dict, provider: Any) -> str:
    selected = str(config.get("provider", "unknown")).strip().lower()
    if selected == "mock" or type(provider).__name__.lower() == "mockprovider":
        raise ValueError("mock_provider_forbidden")
    return selected


def run_live_conformance(
    *,
    config_loader: Callable[[], dict],
    provider_factory: Callable[[dict], Any],
    include_streaming: bool = False,
    timeout_seconds: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
) -> LiveConformanceReport:
    """Run read-only inference checks against the actual configured provider."""
    started = clock()
    checks: list[Check] = []
    provider_name = "unknown"
    response_characters = 0
    config = config_loader()
    if not isinstance(config, dict):
        raise ValueError("provider_configuration_invalid")
    provider = provider_factory(config)
    provider_name = _safe_provider_name(config, provider)
    available = getattr(provider, "is_available", None)
    if not callable(available):
        checks.append(Check("availability", "not_run", "provider_availability_check_unsupported"))
    elif available() is not True:
        checks.append(Check("availability", "fail", "configured_provider_unavailable"))
        checks.append(Check("chat_round_trip", "not_run", "provider_unavailable_no_prompt_sent"))
        if include_streaming:
            checks.append(Check("streaming_round_trip", "not_run", "provider_unavailable_no_prompt_sent"))
        return LiveConformanceReport(
            1, provider_name, "live", tuple(checks),
            max(0.0, clock() - started), 0,
        )
    else:
        checks.append(Check("availability", "pass", "configured_provider_reports_available"))

    adapter = ProviderAgentAdapter(provider, default_timeout_seconds=timeout_seconds)
    structural = inspect_provider_adapter(adapter)
    checks.append(Check(
        "chat_interface", "pass" if structural["capabilities"]["chat"] else "fail",
        structural["evidence"]["chat"],
    ))
    prompt = "Reply with exactly this text and nothing else: YUKI_LIVE_CONFORMANCE_OK"
    messages = [{"role": "user", "content": prompt}]
    try:
        response = adapter(
            messages=messages, tools=[], tool_choice="none",
            timeout_seconds=timeout_seconds,
        )
        text_value = response.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            checks.append(Check("chat_round_trip", "fail", "provider_response_empty_or_invalid"))
        else:
            response_characters = len(text_value)
            checks.append(Check("chat_round_trip", "pass", "live_inference_returned_nonempty_text"))
    except Exception as exc:
        checks.append(Check("chat_round_trip", "fail", "live_inference_failed_" + type(exc).__name__))

    if include_streaming:
        if not structural["capabilities"]["streaming_interface"]:
            checks.append(Check("streaming_round_trip", "not_run", "native_streaming_not_advertised"))
        else:
            try:
                events = adapter.stream_model(
                    messages=messages, tools=[], tool_choice="none",
                    timeout_seconds=timeout_seconds,
                )
                event_count = 0
                text_count = 0
                final_seen = False
                for event in events:
                    event_count += 1
                    if event["type"] == "text_delta":
                        text_count += len(event["text"])
                    elif event["type"] == "response":
                        final_seen = True
                if event_count == 0 or not final_seen:
                    checks.append(Check("streaming_round_trip", "fail", "stream_missing_final_response_event"))
                else:
                    checks.append(Check("streaming_round_trip", "pass", "live_stream_events_validated"))
            except Exception as exc:
                checks.append(Check("streaming_round_trip", "fail", "live_stream_failed_" + type(exc).__name__))

    return LiveConformanceReport(
        1, provider_name, "live", tuple(checks),
        max(0.0, clock() - started), response_characters,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live-request", action="store_true",
                        help="authorize one or two minimal prompts to the configured provider")
    parser.add_argument("--include-streaming", action="store_true",
                        help="also exercise native streaming when the configured provider exposes it")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_request:
        parser.error("refusing live provider contact without --confirm-live-request")
    if not 1 <= args.timeout_seconds <= 120:
        parser.error("--timeout-seconds must be between 1 and 120")
    from core.config import load_config
    from providers.factory import create_provider
    try:
        report = run_live_conformance(
            config_loader=load_config,
            provider_factory=create_provider,
            include_streaming=args.include_streaming,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        payload = {
            "schema_version": 1, "status": "fail",
            "verification_level": "live", "checks": [{
                "name": "provider_setup", "status": "fail",
                "detail": "provider_setup_failed_" + type(exc).__name__,
            }],
        }
        print(json.dumps(payload, indent=2))
        return 1
    print(json.dumps(report.to_dict(), indent=2) if args.json else
          f"Live provider conformance: {report.status.upper()} ({report.provider})")
    return 0 if report.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
