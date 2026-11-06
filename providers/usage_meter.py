"""Provider decorator that persists provider-reported token usage.

This layer records only exact usage metadata supplied by the provider. It never
estimates token counts from characters and never invents pricing.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping
import uuid


class UsageMeteringError(RuntimeError):
    """The provider completed but its usage record could not be persisted."""


class UsageMeteredProvider:
    """Wrap a chat provider and append exact usage to a UsageLedger."""

    def __init__(
        self, provider: Any, *, ledger: Any, provider_id: str, model_id: str,
        session_id: str = "default", agent_id: str | None = None,
        pricing_resolver: Callable[[str, str], Mapping[str, Any] | None] | None = None,
        request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        if provider is None or not callable(getattr(provider, "generate_chat", None)):
            raise TypeError("usage_meter_provider_must_support_generate_chat")
        if ledger is None or not callable(getattr(ledger, "record", None)):
            raise TypeError("usage_ledger_required")
        for value, label, maximum in (
            (provider_id, "provider_id", 48), (model_id, "model_id", 160),
            (session_id, "session_id", 160),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(label + "_invalid")
        if agent_id is not None and (
            not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 160
        ):
            raise ValueError("agent_id_invalid")
        if pricing_resolver is not None and not callable(pricing_resolver):
            raise TypeError("pricing_resolver_must_be_callable")
        if not callable(request_id_factory):
            raise TypeError("request_id_factory_must_be_callable")
        self.provider = provider
        self.ledger = ledger
        self.provider_id = provider_id.strip()
        self.model_id = model_id.strip()
        self.session_id = session_id.strip()
        self.agent_id = agent_id.strip() if agent_id else None
        self.pricing_resolver = pricing_resolver
        self.request_id_factory = request_id_factory

    def _record_usage(self, response: Mapping[str, Any]) -> None:
        usage = response.get("usage")
        if usage is None:
            return
        if not isinstance(usage, Mapping) or set(usage) != {
            "input_tokens", "output_tokens", "estimated"
        }:
            raise UsageMeteringError("provider_usage_metadata_invalid")
        input_tokens = usage["input_tokens"]
        output_tokens = usage["output_tokens"]
        estimated = usage["estimated"]
        if (type(input_tokens) is not int or type(output_tokens) is not int
                or input_tokens < 0 or output_tokens < 0
                or input_tokens > 10_000_000 or output_tokens > 10_000_000
                or type(estimated) is not bool):
            raise UsageMeteringError("provider_usage_metadata_out_of_range")
        if estimated:
            # This integration deliberately persists provider-reported usage only.
            raise UsageMeteringError("estimated_provider_usage_not_supported")
        pricing = None
        if self.pricing_resolver is not None:
            pricing = self.pricing_resolver(self.provider_id, self.model_id)
        if pricing is not None and not isinstance(pricing, Mapping):
            raise UsageMeteringError("pricing_resolver_returned_invalid_value")
        pricing = dict(pricing or {})
        allowed = {
            "input_price_per_million", "output_price_per_million",
            "currency", "price_version",
        }
        if set(pricing) - allowed:
            raise UsageMeteringError("pricing_resolver_returned_unknown_fields")
        provider_request_id = response.get("request_id")
        if provider_request_id is not None and (
            not isinstance(provider_request_id, str) or not provider_request_id.strip()
            or len(provider_request_id) > 160
        ):
            provider_request_id = None
        request_id = provider_request_id or self.request_id_factory()
        try:
            self.ledger.record(
                provider=self.provider_id, model=self.model_id,
                session_id=self.session_id, agent_id=self.agent_id,
                request_id=request_id, input_tokens=input_tokens,
                output_tokens=output_tokens, estimated=False, **pricing,
            )
        except Exception as exc:
            # Do not silently lose billing telemetry after a successful inference.
            raise UsageMeteringError(
                "provider_succeeded_but_usage_persistence_failed"
            ) from exc

    def generate_chat(self, **kwargs):
        response = self.provider.generate_chat(**kwargs)
        if isinstance(response, Mapping):
            self._record_usage(response)
        return response

    def generate_chat_stream(self, **kwargs):
        stream = getattr(self.provider, "generate_chat_stream", None)
        if not callable(stream):
            raise RuntimeError("provider_streaming_not_supported")
        for event in stream(**kwargs):
            if isinstance(event, Mapping) and event.get("type") == "response":
                response = event.get("response")
                if isinstance(response, Mapping):
                    self._record_usage(response)
            yield event


__all__ = ["UsageMeteredProvider", "UsageMeteringError"]
