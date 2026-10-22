"""Provider registry and adapter factory for Yuki's terminal runtime.

Only explicitly implemented adapters can be selected. Ollama and hybrid use the
existing provider implementations; hybrid cloud routing remains task-allow-listed.
"""
from dataclasses import dataclass
from typing import Optional

from providers import BaseProvider, MockProvider, create_provider


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    auth_methods: tuple[str, ...]
    implemented: bool = False


class ProviderManager:
    """Register provider metadata and create configured, validated adapters."""

    def __init__(self):
        self._providers: dict[str, ProviderInfo] = {}
        self._factories = {"mock": MockProvider}
        self._selected: Optional[str] = None

        self.register(ProviderInfo("mock", ("none",), True))
        self.register(ProviderInfo("ollama", ("local_ollama_model",), True),
                      factory=lambda: self._configured_provider("ollama"))
        self.register(ProviderInfo("hybrid", ("local_model; optional_task_allowlisted_cloud",), True),
                      factory=lambda: self._configured_provider("hybrid"))
        self.register(ProviderInfo("openai", ("hybrid_task_allowlist_required",)))
        self.register(ProviderInfo("anthropic", ("not_configured",)))
        self.register(ProviderInfo("gemini", ("not_configured",)))
        self.register(ProviderInfo("local", ("use_ollama",)))
        self._selected = "mock"
        # Honor explicit runtime/config selection, but preserve offline mock mode
        # if configuration is missing or the requested provider is not ready.
        import os
        configured = os.environ.get("YUKI_PROVIDER", "").strip().lower()
        if not configured:
            try:
                from core.config import load_config
                configured = str(load_config().get("provider", "mock")).strip().lower()
            except (ImportError, OSError, RuntimeError, ValueError):
                configured = "mock"
        if configured and configured != "mock":
            self.select(configured)

    @staticmethod
    def _configured_provider(name: str) -> BaseProvider:
        # Import configuration lazily so mock/offline startup does not need to
        # load YAML or require a configured local model.
        from core.config import load_config

        config = load_config()
        config["provider"] = name
        return create_provider(config)

    def register(self, provider: ProviderInfo, factory=None) -> None:
        if not isinstance(provider, ProviderInfo):
            raise TypeError("provider must be a ProviderInfo instance.")
        key = provider.name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty.")
        if not isinstance(provider.auth_methods, tuple):
            raise TypeError("auth_methods must be a tuple.")
        if factory is not None:
            self._factories[key] = factory
        elif not provider.implemented:
            self._factories.pop(key, None)
        elif key not in self._factories:
            raise ValueError("An implemented provider must have an adapter factory.")
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
        try:
            # Validate required settings before changing the active provider.
            self._factories[key]()
        except (ValueError, RuntimeError, OSError) as exc:
            message = str(exc)
            if key in {"ollama", "hybrid"}:
                return f"Could not select {key}: check config.yaml and local model setup. {message}"
            return f"Could not select {key}; current provider remains active."
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
        if not callable(getattr(adapter, "generate", None)) or not callable(
            getattr(adapter, "is_available", None)
        ):
            raise TypeError("Provider factory must return a compatible adapter.")
        return adapter

    def current(self) -> Optional[ProviderInfo]:
        return self._providers.get(self._selected) if self._selected else None
