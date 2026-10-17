"""Provider registry; no credentials are stored here."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProviderInfo:
    name: str
    auth_methods: tuple[str, ...]
    implemented: bool = False


class ProviderManager:
    def __init__(self):
        self._providers: dict[str, ProviderInfo] = {}
        self._selected: Optional[str] = None
        self.register(ProviderInfo("mock", ("none",), True))
        self.register(ProviderInfo("openai", ("api_key", "official_login")))
        self.register(ProviderInfo("anthropic", ("api_key", "official_login")))
        self.register(ProviderInfo("gemini", ("api_key", "official_login")))
        self.register(ProviderInfo("local", ("local_runtime",)))
        self._selected = "mock"

    def register(self, provider: ProviderInfo) -> None:
        key = provider.name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty.")
        self._providers[key] = provider

    def list_providers(self):
        return [{"name": p.name, "auth_methods": list(p.auth_methods),
                 "implemented": p.implemented, "selected": name == self._selected}
                for name, p in sorted(self._providers.items())]

    def select(self, name: str) -> str:
        key = name.strip().lower()
        if key not in self._providers:
            return f"Unknown provider: {key}"
        if not self._providers[key].implemented:
            return f"{key} is registered but not implemented yet. Yuki will keep using its current provider."
        self._selected = key
        return f"Selected provider: {key}"

    def current(self) -> Optional[ProviderInfo]:
        return self._providers.get(self._selected) if self._selected else None
