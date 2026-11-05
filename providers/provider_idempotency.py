"""Explicit provider-side idempotency capability inspection."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderIdempotencyCapability:
    mode: str
    supports_key_parameter: bool
    safe_to_forward_key: bool
    reason: str


def inspect_provider_idempotency(provider: Any) -> ProviderIdempotencyCapability:
    """Inspect declared native idempotency without making a provider request.

    A declaration is not behavioral certification. Live provider conformance
    must separately verify that the provider honors key reuse semantics.
    """
    mode = getattr(provider, "idempotency_mode", "unsupported")
    if mode not in {"native", "unsupported"}:
        return ProviderIdempotencyCapability("unsupported", False, False, "mode_invalid")
    method = getattr(provider, "generate_chat", None)
    if not callable(method):
        return ProviderIdempotencyCapability("unsupported", False, False, "chat_method_missing")
    try:
        parameters = inspect.signature(method).parameters
        supports = "idempotency_key" in parameters
    except (TypeError, ValueError):
        supports = False
    if mode == "native" and supports:
        return ProviderIdempotencyCapability("native", True, True, "declared_native_contract")
    if mode == "native":
        return ProviderIdempotencyCapability("unsupported", False, False, "native_contract_mismatch")
    return ProviderIdempotencyCapability("unsupported", supports, False, "provider_does_not_declare_native_support")


__all__ = ["ProviderIdempotencyCapability", "inspect_provider_idempotency"]
