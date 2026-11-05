"""Shared runtime policy for excluding test-only provider implementations."""
from __future__ import annotations

from typing import Any


def is_mock_provider(provider: Any) -> bool:
    """Identify explicitly marked or conventionally named mock adapters.

    This is a defense-in-depth runtime guard, not a sandbox boundary. Production
    provider registration must still use trusted, reviewed adapter code.
    """
    if provider is None:
        return False
    provider_type = type(provider)
    name = provider_type.__name__.replace("_", "").lower()
    return (
        name in {"mockprovider", "mock"}
        or getattr(provider, "provider_kind", None) == "mock"
        or getattr(provider, "is_test_provider", False) is True
    )


__all__ = ["is_mock_provider"]
