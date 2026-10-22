"""Provider registry aligned with the production provider factory.

Registry status means an adapter can be constructed from current configuration; it
does not guarantee network availability or successful authentication.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from providers import create_provider


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    auth_methods: tuple[str, ...]
    implemented: bool = False


class ProviderManager:
    """Manage selectable providers using the same factory as the runtime."""

    def __init__(self, config: Optional[Mapping[str, object]] = None, *, environ=None):
        self._config = dict(config or {})
        self._environ = os.environ if environ is None else environ
        self._providers: dict[str, ProviderInfo] = {}
        self._factories: dict[str, Callable[[], object]] = {}
        self._selected: Optional[str] = None
        self.register(ProviderInfo("mock", ("none",), True), factory=lambda: create_provider({"provider": "mock"}, environ=self._environ))
        self.register(ProviderInfo("ollama", ("local_runtime",), True), factory=lambda: create_provider(self._provider_config("ollama"), environ=self._environ))
        self.register(ProviderInfo("hybrid", ("local_runtime", "explicit_cloud_configuration"), True), factory=lambda: create_provider(self._provider_config("hybrid"), environ=self._environ))
        self.register(ProviderInfo("openai", ("official_api_key_flow_planned",)))
        self.register(ProviderInfo("anthropic", ("official_api_key_flow_planned",)))
        self.register(ProviderInfo("gemini", ("official_cli_adapter_not_integrated_on_this_branch",)))
        self._selected = "mock"

    def _provider_config(self, name: str) -> dict[str, object]:
        cfg = dict(self._config)
        cfg["provider"] = name
        return cfg

    def register(self, provider: ProviderInfo, factory=None) -> None:
        if not isinstance(provider, ProviderInfo):
            raise TypeError("provider must be a ProviderInfo instance.")
        key = provider.name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty.")
        if not isinstance(provider.auth_methods, tuple) or any(not isinstance(item, str) for item in provider.auth_methods):
            raise TypeError("auth_methods must be a tuple of strings.")
        if provider.implemented and factory is None and key not in self._factories:
            raise ValueError("An implemented provider must have an adapter factory.")
        if factory is not None:
            if not callable(factory):
                raise TypeError("factory must be callable.")
            self._factories[key] = factory
        elif not provider.implemented:
            self._factories.pop(key, None)
        self._providers[key] = ProviderInfo(key, provider.auth_methods, bool(provider.implemented))

    def list_providers(self):
        return [{"name": provider.name, "auth_methods": list(provider.auth_methods),
                 "implemented": provider.implemented, "selected": name == self._selected}
                for name, provider in sorted(self._providers.items())]

    def select(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            return "Provide a provider name."
        key = name.strip().lower()
        if key not in self._providers:
            return f"Unknown provider: {key}"
        if not self._providers[key].implemented or key not in self._factories:
            return f"{key} is registered but not implemented. Yuki will keep using its current provider."
        try:
            self._build_adapter(key)
        except (TypeError, ValueError, RuntimeError) as exc:
            return f"Cannot select {key}: {type(exc).__name__}. Current provider is unchanged."
        self._selected = key
        return f"Selected provider: {key}"

    def _build_adapter(self, key: str):
        adapter = self._factories[key]()
        if not callable(getattr(adapter, "generate", None)) or not callable(getattr(adapter, "is_available", None)):
            raise TypeError("Provider factory must return an adapter with generate() and is_available().")
        return adapter

    def create_selected_adapter(self):
        if self._selected is None or self._selected not in self._factories:
            return None
        return self._build_adapter(self._selected)

    def current(self) -> Optional[ProviderInfo]:
        return self._providers.get(self._selected) if self._selected else None
