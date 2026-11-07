"""Structural conformance inspection for Yuki provider adapters.

Inspection reports interface evidence only. It never proves remote behavior,
security, quality, cancellation compliance, or provider-side idempotency.
"""
from __future__ import annotations

import inspect
from typing import Any


class AdapterConformanceError(ValueError):
    """Adapter does not satisfy the minimum Yuki chat contract."""


def _signature(method: Any):
    try:
        return inspect.signature(method)
    except (TypeError, ValueError):
        return None


def inspect_provider_adapter(adapter: Any) -> dict[str, Any]:
    """Return a sanitized, deterministic capability report for an adapter."""
    provider = getattr(adapter, "provider", None)
    if provider is None:
        raise AdapterConformanceError("adapter_provider_missing")
    generate = getattr(provider, "generate_chat", None)
    if not callable(generate):
        raise AdapterConformanceError("generate_chat_missing")
    sig = _signature(generate)
    if sig is None:
        raise AdapterConformanceError("generate_chat_signature_uninspectable")
    parameters = sig.parameters
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    required = {"messages", "tools", "tool_choice"}
    missing = required - set(parameters)
    if missing and not accepts_kwargs:
        raise AdapterConformanceError("generate_chat_signature_incompatible")
    if any(parameters[name].kind is inspect.Parameter.POSITIONAL_ONLY for name in required if name in parameters):
        raise AdapterConformanceError("generate_chat_signature_incompatible")

    stream = getattr(provider, "generate_chat_stream", None)
    stream_sig = _signature(stream) if callable(stream) else None
    stream_ready = bool(
        callable(stream) and stream_sig is not None
        and (
            {"messages", "tools", "tool_choice"}.issubset(stream_sig.parameters)
            or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in stream_sig.parameters.values())
        )
    )
    context_aware = "execution_context" in parameters or accepts_kwargs
    stream_context_aware = bool(
        stream_ready and (
            "execution_context" in stream_sig.parameters
            or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in stream_sig.parameters.values())
        )
    )
    return {
        "schema_version": 1,
        "adapter_type": type(adapter).__name__,
        "provider_type": type(provider).__name__,
        "capabilities": {
            "chat": True,
            "streaming_interface": stream_ready,
            "cooperative_execution_context": context_aware,
            "streaming_execution_context": stream_context_aware,
        },
        "evidence": {
            "chat": "generate_chat_signature_inspected",
            "streaming_interface": "generate_chat_stream_signature_inspected" if stream_ready else "not_detected",
            "cooperative_execution_context": "execution_context_parameter_detected" if context_aware else "not_detected",
            "streaming_execution_context": "execution_context_parameter_detected" if stream_context_aware else "not_detected",
        },
        "verification_level": "structural_only",
        "limitations": [
            "Signature inspection does not execute provider code.",
            "A declared execution context does not prove that deadlines or cancellation are honored.",
            "Streaming interface detection does not validate yielded event shapes.",
            "Structured tool-call correctness requires runtime contract tests.",
        ],
    }


__all__ = ["AdapterConformanceError", "inspect_provider_adapter"]
