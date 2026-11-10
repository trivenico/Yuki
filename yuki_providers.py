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
        }
        self._selected: Optional[str] = str(self._config.get("provider", self._environ.get("YUKI_PROVIDER", "openai"))).strip().lower()
        self._selected_adapter: Optional[BaseProvider] = None
        for item in (
            ProviderInfo("openai", ("OPENAI_API_KEY",), True),
            ProviderInfo("ollama", ("local_ollama_runtime",), True),
            ProviderInfo("hybrid", ("local_plus_explicit_cloud_api",), True),
            ProviderInfo("gemini", ("official_gemini_cli_account_login",), True),
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
        if key == self._selected:
            self._selected_adapter = None

    def list_providers(self):
        return [{
            "name": p.name, "auth_methods": list(p.auth_methods),
            "implemented": p.implemented and (p.name in self._factories or p.name in {"ollama", "hybrid"}),
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

    def select(self, name: str, *, allow_cloud=False) -> str:
        if not isinstance(name, str) or not name.strip():
            return "Provide a provider name."
        if type(allow_cloud) is not bool:
            raise TypeError("allow_cloud must be a boolean")
        key = name.strip().lower()
        info = self._providers.get(key)
        if info is None:
            return f"Unknown provider: {key}"
        if not info.implemented or (key not in self._factories and key not in {"ollama", "hybrid"}):
            return f"{key} is registered but not implemented. Yuki will keep using its current provider."
        # Enforce cloud consent at the shared provider boundary, not only
        # in browser/UI callers. Catalog privacy is the authoritative routing
        # classification; unknown and third-party destinations fail closed.
        try:
            catalog_entry = next(
                (item for item in self.catalog() if item.get("name") == key), None
            )
            privacy = catalog_entry.get("privacy") if catalog_entry else "unknown"
            if privacy in {"cloud_provider", "third_party_gateway"} and not allow_cloud:
                raise PermissionError("cloud consent is required")
            if privacy == "unknown":
                raise PermissionError("provider privacy is unknown")
            if key in {"openai", "ollama"}:
                adapter = self.create_routed_model_adapter(required=("text",), provider_preference=(key,), allow_cloud=allow_cloud)
            else:
                adapter = self._build_adapter(key)
                # Non-exact-model adapters (for example Gemini CLI and hybrid)
                # must pass the same availability gate before selection commits.
                try:
                    available = adapter.is_available()
                except Exception:
                    available = False
                if available is not True:
                    raise RuntimeError("provider is not available")
        except PermissionError:
            return f"Cannot select {key}: explicit cloud consent is required."
        except Exception as exc:
            return f"Cannot select {key}: provider configuration, availability, or construction failed ({type(exc).__name__})."
        # Commit selected name and availability-checked adapter atomically.
        self._selected = key
        self._selected_adapter = adapter
        return f"Selected provider: {key}"
    def create_selected_adapter(self) -> Optional[BaseProvider]:
        if not self._selected:
            return None
        if self._selected_adapter is None:
            self._selected_adapter = self._build_adapter(self._selected)
        return self._selected_adapter

    def create_selected_agent_model(self):
        """Return the selected provider through the bounded agent chat contract.

        Text-only providers (Gemini CLI and hybrid routing) are rejected rather
        than silently flattening transcripts or routing tool requests elsewhere.
        """
        from providers.agent_adapter import ProviderAgentAdapter

        provider = self.create_selected_adapter()
        if provider is None:
            raise RuntimeError("No provider is selected.")
        if not callable(getattr(provider, "generate_chat", None)):
            raise RuntimeError(
                f"Selected provider '{self._selected}' does not support structured agent chat."
            )
        return ProviderAgentAdapter(provider)

    def current(self) -> Optional[ProviderInfo]:
        return self._providers.get(self._selected)

    def catalog(self):
        """Return sanitized provider/model metadata without making network calls."""
        from providers.catalog import build_provider_catalog
        entries = build_provider_catalog(
            config=self._config, environ=self._environ, provider_manager=self
        )
        for item in entries:
            item["selected"] = item.get("name") == self._selected
        return entries

    def routing_registry(self):
        """Return a capability-filtered registry for explicit, policy-aware routing."""
        from providers.capabilities import registry_from_catalog
        return registry_from_catalog(self.catalog())

    def model_capability_registry(self):
        """Build exact-model profiles from the sanitized, configured adapter catalog.

        Only the text capability is asserted by the current provider adapter
        contract. Modality, tool-calling, reasoning and context-window support
        remain unknown until separately verified for an exact model.
        """
        from providers.model_registry import ModelCapabilityProfile, ModelCapabilityRegistry

        profiles = []
        for item in self.catalog():
            provider = item.get("name")
            model = item.get("configured_model")
            privacy = item.get("privacy")
            if provider not in {"openai", "ollama"} or not isinstance(model, str):
                continue
            # The current exact-model identifier contract is provider/model.
            # Do not rewrite or guess identifiers that do not fit it.
            model_id = f"{provider}/{model}"
            try:
                profile = ModelCapabilityProfile(
                    provider=provider,
                    model_id=model_id,
                    capabilities=frozenset({"text"}),
                    implemented=item.get("implemented") is True,
                    ready=item.get("ready") is True,
                    privacy=(
                        "local_runtime" if provider == "ollama" and privacy == "local_runtime"
                        else "cloud_provider" if provider == "openai" and privacy == "cloud_provider"
                        else "unknown"
                    ),
                    context_window=None,
                    evidence_source="adapter_contract",
                )
            except (TypeError, ValueError):
                # Invalid or ambiguous catalog data is excluded, never repaired
                # into a routable profile.
                continue
            profiles.append(profile)
        return ModelCapabilityRegistry(profiles)

    def create_routed_model_adapter(
        self, *, required=("text",), provider_preference=(), model_preference=(),
        allow_cloud=False, min_context_window=None,
    ):
        """Select and instantiate one exact model under capability/privacy policy.

        This production route is intentionally limited to adapters whose model
        identifier can be explicitly pinned at construction (OpenAI and local
        Ollama today). It never falls back to a different model or provider.
        """
        from providers.factory import create_provider as build_provider

        if type(allow_cloud) is not bool:
            raise TypeError("allow_cloud must be a boolean")
        if isinstance(provider_preference, (str, bytes)):
            raise TypeError("provider_preference must be an iterable, not text")
        try:
            preferences = tuple(islice(iter(provider_preference), 33))
        except TypeError as exc:
            raise TypeError("provider_preference must be iterable") from exc
        if len(preferences) > 32:
            raise ValueError("at most 32 provider preferences are allowed")
        selected = self.model_capability_registry().route(
            required=required,
            provider_preference=preferences,
            model_preference=model_preference,
            allow_cloud=allow_cloud,
            strict_provider_preference=bool(preferences),
            min_context_window=min_context_window,
        )
        provider, model = selected.provider, selected.model_id.split("/", 1)[1]
        config = dict(self._config)
        environ = dict(self._environ)
        config["provider"] = provider
        if provider == "openai":
            environ["YUKI_OPENAI_MODEL"] = model
        elif provider == "ollama":
            config["ollama_model"] = model
        else:
            raise RuntimeError("selected provider has no exact-model construction path")
        adapter = build_provider(config, environ=environ)
        if not callable(getattr(adapter, "generate", None)) or not callable(
            getattr(adapter, "is_available", None)
        ):
            raise TypeError("Provider factory returned an invalid adapter.")
        try:
            available = adapter.is_available()
        except Exception:
            available = False
        if available is not True:
            raise RuntimeError(
                f"Selected model '{selected.model_id}' is unavailable; no fallback was attempted."
            )
        return adapter

    def create_selected_runtime_adapter(self, *, allow_cloud=False):
        """Construct the selected provider through exact-model routing where supported.

        OpenAI cloud use must be explicitly authorized by the caller. Providers
        without exact model pinning retain their existing adapter path.
        """
        if type(allow_cloud) is not bool:
            raise TypeError("allow_cloud must be a boolean")
        selected = self._selected
        if selected in {"openai", "ollama"} and self._selected_adapter is not None:
            return self._selected_adapter
        if selected in {"openai", "ollama"}:
            return self.create_routed_model_adapter(
                required=("text",),
                provider_preference=(selected,),
                allow_cloud=allow_cloud,
            )
        return self.create_selected_adapter()

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
