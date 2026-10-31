"""Provider registry backed by the same factory used by the application runtime."""
from __future__ import annotations
import os
from itertools import islice
from dataclasses import dataclass
from typing import Callable, Optional
from providers import BaseProvider, create_provider
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
        self._factories: dict[str, Callable] = {
            "openai": lambda: create_provider({**self._config, "provider": "openai"}, environ=self._environ),
            "gemini": lambda: create_provider({**self._config, "provider": "gemini"}, environ=self._environ),
            "colibri": lambda: create_provider({**self._config, "provider": "colibri"}, environ=self._environ),
        }
        self._selected: Optional[str] = str(self._config.get("provider", self._environ.get("YUKI_PROVIDER", "openai"))).strip().lower()
        self._selected_adapter: Optional[BaseProvider] = None
        for item in (
            ProviderInfo("openai", ("OPENAI_API_KEY",), True),
            ProviderInfo("ollama", ("local_ollama_runtime",), True),
            ProviderInfo("hybrid", ("local_plus_explicit_cloud_api",), True),
            ProviderInfo("gemini", ("official_gemini_cli_account_login",), True),
            ProviderInfo("colibri", ("local_colibri_runtime",), True),
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
        elif key not in self._factories and key not in {"ollama", "hybrid", "colibri"}:
            raise ValueError("An implemented provider must have an adapter factory.")
        self._providers[key] = ProviderInfo(key, provider.auth_methods, bool(provider.implemented))
        if key == self._selected:
            self._selected_adapter = None

    def list_providers(self):
        return [{
            "name": p.name, "auth_methods": list(p.auth_methods),
            "implemented": p.implemented and (p.name in self._factories or p.name in {"ollama", "hybrid", "colibri"}),
            "selected": name == self._selected,
        } for name, p in sorted(self._providers.items())]

    def _build_adapter(self, name: str):
        if name in {"ollama", "hybrid"}:
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
        if not info.implemented or (key not in self._factories and key not in {"ollama", "hybrid", "colibri"}):
            return f"{key} is registered but not implemented. Yuki will keep using its current provider."
        try:
            adapter = self._build_adapter(key)
        except Exception as exc:
            return f"Cannot select {key}: provider configuration or construction failed ({type(exc).__name__})."
        # Commit provider selection and its adapter together. A second factory call
        # could fail after the selected name changed, leaving status and runtime split.
        self._selected = key
        self._selected_adapter = adapter
        return f"Selected provider: {key}"

    def create_selected_adapter(self) -> Optional[BaseProvider]:
        if not self._selected:
            return None
        if self._selected_adapter is None:
            self._selected_adapter = self._build_adapter(self._selected)
        return self._selected_adapter

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

    def create_routed_adapter(self, *, required=("text",), preference=(), allow_cloud=False):
        """Create the explicitly routed adapter; never fall back after selection.

        Cloud providers require allow_cloud=True. The capability catalog is
        metadata-only; this method performs the selected adapter's availability
        check before returning it. A failed check is terminal for this route.
        """
        if isinstance(preference, (str, bytes)):
            raise TypeError("preference must be an iterable of provider names, not text.")
        try:
            preferences = tuple(islice(iter(preference), 33))
        except TypeError as exc:
            raise TypeError("preference must be iterable.") from exc
        if len(preferences) > 32:
            raise ValueError("at most 32 provider preferences are allowed.")
        selected = self.routing_registry().route(
            required=required, preference=preferences, allow_cloud=allow_cloud,
            strict_preference=bool(preferences),
        )
        adapter = self._build_adapter(selected.provider)
        try:
            available = adapter.is_available()
        except Exception:
            available = False
        if available is not True:
            raise RuntimeError(
                f"Routed provider '{selected.provider}' is not available; no fallback was attempted."
            )
        return adapter
