"""Provider registry backed by the same factory used by the application runtime."""
from __future__ import annotations
import os
from itertools import islice
from dataclasses import dataclass
from typing import Callable, Optional
from providers import BaseProvider, create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.openai_compatible import OpenAICompatibleProvider
from urllib.parse import urlsplit
import re

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
        self._compatible_profiles: dict[str, dict] = {}
        self._vault_profiles: dict[str, dict] = {}
        self._profile_owner_id: Optional[str] = None
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

    def register_openai_compatible(self, *, name: str, base_url: str, model: str,
                                   api_key_env: str, allowed_hosts=None, select: bool = False) -> ProviderInfo:
        """Register an explicitly trusted OpenAI-compatible endpoint profile.

        Only the environment-variable name is stored, never a credential value.
        Provider-native protocols require their own adapter.
        """
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", name):
            raise ValueError("custom provider name must be a lowercase stable identifier")
        if name in {"openai", "ollama", "gemini", "hybrid", "mock"}:
            raise ValueError("custom provider name is reserved")
        if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", api_key_env):
            raise ValueError("api_key_env must be an uppercase environment variable name")
        parsed = urlsplit(base_url if isinstance(base_url, str) else "")
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme.lower() != "https" or not host:
            raise ValueError("custom cloud endpoints must use HTTPS")
        if allowed_hosts is not None:
            if isinstance(allowed_hosts, (str, bytes)):
                raise TypeError("allowed_hosts must be an iterable of exact hostnames")
            approved = {str(item).strip().rstrip(".").lower() for item in allowed_hosts}
            if host not in approved:
                raise ValueError("endpoint hostname must be explicitly included in allowed_hosts")
        else:
            approved = {host}
        profile = {"name": name, "base_url": base_url, "model": model,
                   "api_key_env": api_key_env, "allowed_hosts": tuple(sorted(approved))}
        def factory(profile=profile):
            return OpenAICompatibleProvider(
                profile["model"], api_key_env=profile["api_key_env"],
                base_url=profile["base_url"], allowed_hosts=profile["allowed_hosts"],
                environ=self._environ,
            )
        self.register(ProviderInfo(name, ("environment_variable",), True), factory=factory)
        self._compatible_profiles[name] = profile
        if select:
            result = self.select(name)
            if not result.startswith("Selected provider:"):
                raise RuntimeError("custom provider could not be selected")
        return self._providers[name]

    def compatible_profiles(self) -> list[dict]:
        """Return endpoint metadata without credentials or secret values."""
        return [
            {key: value for key, value in profile.items() if key != "api_key_env"}
            | {"credential_configured": bool(self._environ.get(profile["api_key_env"], "").strip())}
            for profile in sorted(self._compatible_profiles.values(), key=lambda item: item["name"])
        ]
    def load_persisted_profiles(self, store, owner_id: str) -> tuple[str, ...]:
        """Register owner-scoped profiles backed by the OS credential vault.

        The caller must supply an already authenticated owner identity. Secrets
        are resolved only when a provider adapter is constructed, never during
        profile listing or catalog generation.
        """
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("an authenticated owner identity is required")
        if self._profile_owner_id is not None and self._profile_owner_id != owner_id:
            raise PermissionError("provider manager is already bound to a different owner")
        if not callable(getattr(store, "list_profiles", None)) or not callable(
            getattr(store, "resolve_for_runtime", None)
        ):
            raise TypeError("store must implement the provider profile runtime contract")
        self._profile_owner_id = owner_id
        loaded = []
        for profile in store.list_profiles(owner_id):
            name = getattr(profile, "name", None)
            if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", name)
                    or getattr(profile, "owner_id", None) != owner_id):
                raise ValueError("persisted provider profile identity is invalid")
            if name in self._providers and name not in self._vault_profiles:
                raise ValueError("persisted profile conflicts with a registered provider")
            def factory(profile_name=name, profile_owner=owner_id, profile_store=store):
                resolved = profile_store.resolve_for_runtime(profile_owner, profile_name)
                key_env = "YUKI_VAULT_PROFILE_API_KEY"
                adapter_environ = {key_env: resolved["api_key"]}
                return OpenAICompatibleProvider(
                    resolved["model"], api_key_env=key_env,
                    base_url=resolved["base_url"],
                    allowed_hosts={resolved["allowed_host"]},
                    environ=adapter_environ,
                )
            self.register(
                ProviderInfo(name, ("os_credential_vault",), True),
                factory=factory,
            )
            self._vault_profiles[name] = {
                "name": name, "owner_id": owner_id,
                "base_url": profile.base_url, "model": profile.model,
                "allowed_host": profile.allowed_host, "store": store,
            }
            loaded.append(name)
        return tuple(loaded)

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
        catalog = build_provider_catalog(
            config=self._config, environ=self._environ, provider_manager=self
        )
        known = {item["name"] for item in catalog}
        for profile in self.compatible_profiles():
            if profile["name"] in known:
                continue
            configured = profile["credential_configured"]
            catalog.append({
                "name": profile["name"], "label": profile["name"],
                "implemented": True, "ready": configured,
                "privacy": "cloud_provider", "capabilities": ["text", "tooling_provider_dependent"],
                "configured_model": profile["model"],
                "credential_configured": configured,
                "selected": profile["name"] == (self._selected or ""),
                "status": "api_key_configured" if configured else "api_key_not_configured",
            })
        return catalog

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
