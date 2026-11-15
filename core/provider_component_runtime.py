"""Lifecycle adapter for a concrete configured model provider.

Provider construction remains the provider factory's responsibility. This
adapter gives the canonical component registry an explicit startup, health,
and shutdown contract without pretending that construction proves availability.
"""
from __future__ import annotations

import threading
from typing import Any

from core.component_catalog import ComponentContract, DEFAULT_COMPONENTS
from core.component_registry import ComponentRuntime


def build_provider_component_runtime(
    provider: Any,
    *,
    contract: ComponentContract | None = None,
) -> ComponentRuntime:
    """Bind a provider to the canonical provider-runtime component contract.

    The provider must expose is_available() -> bool. Startup performs one
    readiness probe and fails closed if it is unavailable. Health probes are
    only meaningful after successful startup. Shutdown invokes a provider's
    optional close method when present; providers without persistent resources
    need no synthetic cleanup hook.
    """
    if provider is None:
        raise TypeError("provider must be a concrete provider instance")
    availability = getattr(provider, "is_available", None)
    if not callable(availability):
        raise TypeError("provider must expose a callable is_available method")
    selected_contract = contract or next(
        item for item in DEFAULT_COMPONENTS
        if item.component_id == "provider-runtime"
    )
    if not isinstance(selected_contract, ComponentContract):
        raise TypeError("contract must be a ComponentContract")
    if selected_contract.component_id != "provider-runtime":
        raise ValueError("contract must describe provider-runtime")

    lock = threading.RLock()
    started = False
    closed = False

    def start() -> None:
        nonlocal started
        with lock:
            if closed:
                raise RuntimeError("provider component runtime is closed")
            if started:
                return
            try:
                ready = availability()
            except Exception as exc:
                raise RuntimeError(
                    f"provider availability probe failed: {type(exc).__name__}"
                ) from exc
            if not isinstance(ready, bool):
                raise TypeError("provider is_available() must return bool")
            if not ready:
                raise RuntimeError("configured provider is unavailable")
            started = True

    def health_check() -> bool:
        with lock:
            if closed or not started:
                return False
            ready = availability()
            if not isinstance(ready, bool):
                raise TypeError("provider is_available() must return bool")
            return ready

    def close() -> None:
        nonlocal closed
        with lock:
            if closed:
                return
            closed = True
            close_provider = getattr(provider, "close", None)
            if callable(close_provider):
                close_provider()

    return ComponentRuntime(
        contract=selected_contract,
        instance=provider,
        start=start,
        close=close,
        health_check=health_check,
    )
