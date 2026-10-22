"""Provider registry backed by the same factory used by the application runtime."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable, Optional
from providers import BaseProvider, MockProvider, create_provider
from providers.gemini_cli import GeminiCLIProvider

@dataclass(frozen=True)
class ProviderInfo:
    name: str
    auth_methods: tuple[str, ...]
    implemented: bool = False

class ProviderManager:
    """Provider capability registry; availability is checked separately from construction."""
    def __init__(self, config=None, *, environ=None):
        self._config = dict(config or {})
        self._environ = os.environ if environ is None else environ
        self._providers: dict[str, ProviderInfo] = {}
        self._factories: dict[str, Callable] = {"mock": MockProvider, "gemini": lambda: create_provider({**self._config, "provider": "gemini"}, environ=self._environ)}
        self._selected: Optional[str] = "mock"
        for item in (
            ProviderInfo("mock", ("none",), True),
            ProviderInfo("ollama", ("local_ollama_runtime",), True),
            ProviderInfo("hybrid", ("local_plus_explicit_cloud_api",), True),
            ProviderInfo("gemini", ("official_gemini_cli_account_login",), True),
            ProviderInfo("openai", ("official_api_key_flow_planned",)),
            ProviderInfo("anthropic", ("official_api_key_flow_planned",)),
            ProviderInfo("deepseek", ("official_api_key_flow_planned",)),
            ProviderInfo("kimi", ("official_api_key_flow_planned",)),
            ProviderInfo("minimax", ("official_api_key_flow_planned",)),
        ):
            self.register(item)

    def register(self, provider: ProviderInfo, factory=None) -> None:
        if not isinstance(provider, ProviderInfo):
            raise TypeError("provider must be a ProviderInfo instance.")
        key = provider.name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty.")
        if not isinstance(provider.auth_methods, tuple):
            raise TypeError("auth_methods must be a tuple.")
        if factory is not None:
            if not callable(factory):
                raise TypeError("factory must be callable.")
            self._factories[key] = factory
        elif not provider.implemented:
            self._factories.pop(key, None)
        elif key not in self._factories and key not in {"ollama", "hybrid"}:
            raise ValueError("An implemented provider must have an adapter factory.")
        self._providers[key] = ProviderInfo(key, provider.auth_methods, bool(provider.implemented))

    def list_providers(self):
        return [{
            "name": p.name, "auth_methods": list(p.auth_methods),
            "implemented": p.implemented and (p.name in self._factories or p.name in {"ollama", "hybrid"}),
            "selected": name == self._selected,
        } for name, p in sorted(self._providers.items())]

    def _build_adapter(self, name: str):
        if name in {"mock", "ollama", "hybrid"}:
            adapter = create_provider({**self._config, "provider": name}, environ=self._environ)
        else:
            factory = self._factories.get(name)
            if factory is None:
                raise ValueError("Provider is not implemented.")
            adapter = factory()
        if not callable(getattr(adapter, "generate", None)) or not callable(getattr(adapter, "is_available", None)):
            raise TypeError("Provider factory must return an adapter with generate() and is_available().")
        return adapter

    def select(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            return "Provide a provider name."
        key = name.strip().lower()
        info = self._providers.get(key)
        if info is None:
            return f"Unknown provider: {key}"
        if not info.implemented or (key not in self._factories and key not in {"ollama", "hybrid"}):
            return f"{key} is registered but not implemented. Yuki will keep using its current provider."
        try:
            self._build_adapter(key)
        except Exception as exc:
            return f"Cannot select {key}: provider configuration or construction failed ({type(exc).__name__})."
        self._selected = key
        return f"Selected provider: {key}"

    def create_selected_adapter(self) -> Optional[BaseProvider]:
        return self._build_adapter(self._selected) if self._selected else None

    def current(self) -> Optional[ProviderInfo]:
        return self._providers.get(self._selected)

    def catalog(self):
        """Return sanitized provider/model metadata without making network calls."""
        from providers.catalog import build_provider_catalog
        return build_provider_catalog(
            config=self._config, environ=self._environ, provider_manager=self
        )

    def routing_registry(self):
        """Return a capability-filtered registry for explicit, policy-aware routing."""
        from providers.capabilities import registry_from_catalog
        return registry_from_catalog(self.catalog())
