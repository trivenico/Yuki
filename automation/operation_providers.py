"""Provider capability contracts for safe workflow operation integration.

This module deliberately does not dispatch provider calls. It validates declared
capabilities so orchestration can refuse unsafe retries or unsupported recovery.
Provider responses remain untrusted data and must be validated by adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol


_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


class OperationProvider(Protocol):
    """Minimal provider contract; implementations own transport and response parsing."""

    def execute(
        self, action: str, request: Mapping[str, Any], *, idempotency_key: str
    ) -> Mapping[str, Any]:
        """Execute once using the supplied stable provider idempotency key."""
        ...

    def lookup(self, *, idempotency_key: str) -> Mapping[str, Any] | None:
        """Return a provider-confirmed operation result, or None when not found."""
        ...


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_idempotency_key: bool
    supports_operation_lookup: bool

    def __post_init__(self) -> None:
        if type(self.supports_idempotency_key) is not bool:
            raise ValueError("supports_idempotency_key must be a boolean.")
        if type(self.supports_operation_lookup) is not bool:
            raise ValueError("supports_operation_lookup must be a boolean.")


@dataclass(frozen=True)
class RegisteredProvider:
    name: str
    provider: OperationProvider
    capabilities: ProviderCapabilities


class ProviderCapabilityError(RuntimeError):
    """Raised when a provider cannot safely support a requested operation."""


class OperationProviderRegistry:
    """Explicit, process-local provider registry; registrations are never overwritten."""

    def __init__(self) -> None:
        self._providers: dict[str, RegisteredProvider] = {}

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not _PROVIDER_NAME.fullmatch(name):
            raise ValueError("provider name must be a bounded lowercase identifier.")
        return name

    def register(
        self, name: str, provider: OperationProvider,
        capabilities: ProviderCapabilities,
    ) -> RegisteredProvider:
        key = self._name(name)
        if not isinstance(capabilities, ProviderCapabilities):
            raise TypeError("capabilities must be ProviderCapabilities.")
        if not callable(getattr(provider, "execute", None)):
            raise TypeError("provider must implement execute().")
        if not callable(getattr(provider, "lookup", None)):
            raise TypeError("provider must implement lookup().")
        if key in self._providers:
            raise ProviderCapabilityError("provider name is already registered.")
        registered = RegisteredProvider(key, provider, capabilities)
        self._providers[key] = registered
        return registered

    def get(self, name: str) -> RegisteredProvider:
        key = self._name(name)
        try:
            return self._providers[key]
        except KeyError as exc:
            raise KeyError("provider is not registered.") from exc

    def require_idempotent_dispatch(self, name: str) -> RegisteredProvider:
        registered = self.get(name)
        if not registered.capabilities.supports_idempotency_key:
            raise ProviderCapabilityError(
                "provider does not guarantee idempotency-key support; dispatch is unsafe."
            )
        return registered

    def require_operation_lookup(self, name: str) -> RegisteredProvider:
        registered = self.get(name)
        if not registered.capabilities.supports_operation_lookup:
            raise ProviderCapabilityError(
                "provider does not support operation lookup; automatic reconciliation is unavailable."
            )
        return registered

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))
