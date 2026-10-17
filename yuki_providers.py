"""Provider registry metadata; no credentials or authentication are implemented here."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProviderInfo:
    name: str
    auth_methods: tuple[str, ...]
    implemented: bool = False


class ProviderManager:
    """Register provider adapters and select only implemented providers."""

    def __init__(self):
        self._providers: dict[str, ProviderInfo] = {}
        self._selected: Optional[str] = None
        self.register(ProviderInfo("mock", ("none",), True))
        self.register(ProviderInfo("openai", ("api_key_planned",)))
        self.register(ProviderInfo("anthropic", ("api_key_planned",)))
        self.register(ProviderInfo("gemini", ("api_key_planned",)))
        self.register(ProviderInfo("local", ("local_runtime_planned",)))
        self._selected = "mock"

    def register(self, provider: ProviderInfo) -> None:
        if not isinstance(provider, ProviderInfo):
            raise TypeError("provider must be a ProviderInfo instance.")
        key = provider.name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty.")
        if not isinstance(provider.auth_methods, tuple):
            raise TypeError("auth_methods must be a tuple.")
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
        if not self._providers[key].implemented:
            return f"{key} is registered but not implemented. Yuki will keep using its current provider."
        self._selected = key
        return f"Selected provider: {key}"

    def current(self) -> Optional[ProviderInfo]:
        return self._providers.get(self._selected) if self._selected else None
