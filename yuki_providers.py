"""Provider registry and adapter factory.

Only the offline mock adapter is implemented. Registry metadata never implies
that a provider is authenticated or ready.
"""

from dataclasses import dataclass
from typing import Optional

from providers import BaseProvider, MockProvider


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    auth_methods: tuple[str, ...]
    implemented: bool = False


class ProviderManager:
    """Register provider metadata and create only implemented adapters."""

    def __init__(self):
        self._providers: dict[str, ProviderInfo] = {}
        self._factories = {"mock": MockProvider}
        self._selected: Optional[str] = None

        self.register(ProviderInfo("mock", ("none",), True))
        self.register(ProviderInfo("openai", ("official_api_key_flow_planned",)))
        self.register(ProviderInfo("anthropic", ("official_api_key_flow_planned",)))
        self.register(ProviderInfo("gemini", ("official_api_key_flow_planned",)))
        self.register(ProviderInfo("local", ("local_runtime_planned",)))
        self._selected = "mock"

    def register(self, provider: ProviderInfo, factory=None) -> None:
        if not isinstance(provider, ProviderInfo):
            raise TypeError("provider must be a ProviderInfo instance.")
        key = provider.name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty.")
        if not isinstance(provider.auth_methods, tuple):
            raise TypeError("auth_methods must be a tuple.")
        if provider.implemented and factory is None and key not in self._factories:
            raise ValueError("An implemented provider must have an adapter factory.")
        if factory is not None:
            self._factories[key] = factory
        else:
            self._factories.pop(key, None)
        self._providers[key] = ProviderInfo(
            name=key,
            auth_methods=provider.auth_methods,
            implemented=bool(provider.implemented),
        )

    def list_providers(self):
        return [
            {
                "name": provider.name,
                "auth_methods": list(provider.auth_methods),
                "implemented": provider.implemented,
                "selected": name == self._selected,
            }
            for name, provider in sorted(self._providers.items())
        ]

    def select(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            return "Provide a provider name."
        key = name.strip().lower()
        if key not in self._providers:
            return f"Unknown provider: {key}"
        if not self._providers[key].implemented or key not in self._factories:
            return f"{key} is registered but not implemented. Yuki will keep using its current provider."
        self._selected = key
        return f"Selected provider: {key}"

    def create_selected_adapter(self) -> Optional[BaseProvider]:
        """Return a fresh adapter for the selected provider, or None if unavailable."""
        if self._selected is None:
            return None
        factory = self._factories.get(self._selected)
        if factory is None:
            return None
        adapter = factory()
        if not isinstance(adapter, BaseProvider):
            raise TypeError("Provider factory must return a BaseProvider.")
        return adapter

    def current(self) -> Optional[ProviderInfo]:
        return self._providers.get(self._selected) if self._selected else None
