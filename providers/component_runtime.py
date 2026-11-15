"""Bind a concrete model provider to Yuki's canonical component lifecycle."""
from __future__ import annotations

from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime


def build_provider_component_runtime(provider, *, contract: ComponentContract | None = None) -> ComponentRuntime:
    """Create lifecycle hooks for one concrete provider instance.

    Startup and readiness use the provider's real availability probe. Shutdown
    delegates to its optional close method; providers without owned resources
    need no cleanup. This adapter never fabricates provider health.
    """
    if provider is None:
        raise TypeError("provider must be a concrete instance.")
    if not callable(getattr(provider, "generate", None)):
        raise TypeError("provider must implement generate(prompt).")
    available = getattr(provider, "is_available", None)
    if not callable(available):
        raise TypeError("provider must implement is_available().")

    if contract is None:
        contract = next(
            (item for item in DEFAULT_COMPONENTS if item.component_id == "provider-runtime"),
            None,
        )
    if not isinstance(contract, ComponentContract) or contract.component_id != "provider-runtime":
        raise ValueError("contract must be Yuki's provider-runtime component contract.")

    close_hook = getattr(provider, "close", None)
    if close_hook is not None and not callable(close_hook):
        raise TypeError("provider close attribute must be callable when present.")

    def check_available() -> bool:
        result = available()
        if not isinstance(result, bool):
            raise TypeError("provider is_available() must return bool.")
        return result

    def start() -> None:
        if not check_available():
            raise RuntimeError("configured provider is not available.")

    def close() -> None:
        if close_hook is not None:
            close_hook()

    return ComponentRuntime(
        contract=contract,
        instance=provider,
        start=start,
        close=close,
        health_check=check_available,
    )
