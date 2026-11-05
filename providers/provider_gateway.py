"""Central execution gateway for real provider-backed agent requests."""
from __future__ import annotations
from typing import Any
from providers.agent_adapter import ProviderAgentAdapter
from providers.provider_policy import is_mock_provider


class ProviderGateway(ProviderAgentAdapter):
    """Policy-enforcing adapter boundary with no implicit provider fallback."""
    def __init__(self, provider: Any, *, provider_id: str,
                 cloud_opt_in: bool = False, default_timeout_seconds: float = 60.0):
        if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 120:
            raise ValueError("provider_id_invalid")
        if type(cloud_opt_in) is not bool:
            raise TypeError("cloud_opt_in_must_be_boolean")
        if is_mock_provider(provider):
            raise ValueError("mock_provider_forbidden")
        privacy = getattr(provider, "privacy_class", "unknown")
        if privacy == "cloud_provider" and not cloud_opt_in:
            raise PermissionError("cloud_provider_requires_explicit_opt_in")
        super().__init__(provider, default_timeout_seconds=default_timeout_seconds)
        self.provider_id = provider_id.strip()
        self.privacy_class = privacy
        self.cloud_opt_in = cloud_opt_in

    @classmethod
    def from_adapter(cls, adapter: ProviderAgentAdapter, *, cloud_opt_in: bool = False):
        if not isinstance(adapter, ProviderAgentAdapter):
            raise TypeError("provider_adapter_required")
        provider = adapter.provider
        provider_id = getattr(provider, "provider_id", None)
        if not isinstance(provider_id, str) or not provider_id.strip():
            provider_name = type(provider).__name__.removesuffix("Provider").removesuffix("Model")
            provider_id = provider_name.lower()
        return cls(provider, provider_id=provider_id, cloud_opt_in=cloud_opt_in,
                   default_timeout_seconds=adapter.default_timeout_seconds)


__all__ = ["ProviderGateway"]
