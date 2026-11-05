"""Trusted, versioned registry for production provider adapters.

Factories are registered by trusted application code. Registry metadata is
declarative; resolving a provider verifies the constructed adapter against it.
No discovery, network request, credential access, or fallback occurs here.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from providers.agent_adapter import ProviderAgentAdapter
from providers.provider_gateway import ProviderGateway
from providers.provider_policy import is_mock_provider

_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")
_VERSION = re.compile(r"^[0-9]+\\.[0-9]+\\.[0-9]+$")
_CAPABILITIES = frozenset({
    "chat", "streaming", "tool_calling", "vision", "embeddings",
    "reranking", "speech_input", "speech_output", "code",
})
_PRIVACY = frozenset({"local", "cloud_provider", "unknown"})


@dataclass(frozen=True)
class ProviderRegistration:
    provider_id: str
    version: str
    privacy_class: str
    capabilities: frozenset[str]
    factory: Callable[[], ProviderAgentAdapter]


class ProviderRegistry:
    """Thread-safe registry; duplicate IDs require an explicit new registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._registrations: dict[str, ProviderRegistration] = {}

    def register(
        self, *, provider_id: str, version: str, privacy_class: str,
        capabilities: set[str] | frozenset[str],
        factory: Callable[[], ProviderAgentAdapter],
    ) -> ProviderRegistration:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError("provider_version_invalid")
        if privacy_class not in _PRIVACY:
            raise ValueError("provider_privacy_class_invalid")
        if not isinstance(capabilities, (set, frozenset)) or not capabilities:
            raise ValueError("provider_capabilities_required")
        if not capabilities <= _CAPABILITIES:
            raise ValueError("provider_capability_unsupported")
        if not callable(factory):
            raise TypeError("provider_factory_must_be_callable")
        registration = ProviderRegistration(
            provider_id=provider_id, version=version,
            privacy_class=privacy_class,
            capabilities=frozenset(capabilities), factory=factory,
        )
        with self._lock:
            if provider_id in self._registrations:
                raise ValueError("provider_already_registered")
            self._registrations[provider_id] = registration
        return registration

    def describe(self) -> list[dict[str, Any]]:
        """Return safe metadata only; never expose factories or credentials."""
        with self._lock:
            return [
                {
                    "provider_id": item.provider_id,
                    "version": item.version,
                    "privacy_class": item.privacy_class,
                    "capabilities": sorted(item.capabilities),
                }
                for item in sorted(self._registrations.values(),
                                   key=lambda value: value.provider_id)
            ]

    def resolve(
        self, provider_id: str, *, required_capabilities: set[str] | frozenset[str] = frozenset(),
        cloud_opt_in: bool = False,
    ) -> ProviderGateway:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")
        if not isinstance(required_capabilities, (set, frozenset)):
            raise TypeError("required_capabilities_must_be_a_set")
        if not required_capabilities <= _CAPABILITIES:
            raise ValueError("required_capability_unsupported")
        with self._lock:
            registration = self._registrations.get(provider_id)
        if registration is None:
            raise LookupError("provider_not_registered")
        missing = required_capabilities - registration.capabilities
        if missing:
            raise ValueError("provider_capability_requirement_not_met")
        adapter = registration.factory()
        if not isinstance(adapter, ProviderAgentAdapter):
            raise TypeError("registered_factory_must_return_ProviderAgentAdapter")
        if is_mock_provider(adapter.provider):
            raise ValueError("mock_provider_forbidden")
        actual_privacy = getattr(adapter.provider, "privacy_class", "unknown")
        if actual_privacy != registration.privacy_class:
            raise ValueError("provider_privacy_metadata_mismatch")
        return ProviderGateway.from_adapter(adapter, cloud_opt_in=cloud_opt_in)


__all__ = ["ProviderRegistration", "ProviderRegistry"]
