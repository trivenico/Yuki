"""Provider registry backed by the same factory used by the application runtime."""
from __future__ import annotations
import os
from contextlib import contextmanager
from itertools import islice
from dataclasses import dataclass
from typing import Callable, Optional
from threading import RLock
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
        configured_provider = self._config.get("provider")
        if configured_provider is None:
            configured_provider = self._environ.get("YUKI_PROVIDER")
        selected_name = (
            str(configured_provider).strip().lower()
            if isinstance(configured_provider, str) and configured_provider.strip()
            else None
        )
        self._selected: Optional[str] = (
            None if selected_name in {"none", "native", "native-only"} else selected_name
        )
        self._selected_adapter: Optional[BaseProvider] = None
        self._retired_adapters: list[BaseProvider] = []
        self._routed_adapters: dict[int, BaseProvider] = {}
        self._lifecycle_lock = RLock()
        self._closed = False
        self._closing = False
        self._active_leases: dict[int, int] = {}
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
        if factory is not None and not callable(factory):
            raise TypeError("factory must be callable.")
        with self._lifecycle_lock:
            self._ensure_open()
            if factory is not None:
                self._factories[key] = factory
            elif not provider.implemented:
                self._factories.pop(key, None)
            elif key not in self._factories and key not in {"ollama", "hybrid"}:
                raise ValueError("An implemented provider must have an adapter factory.")
            self._providers[key] = ProviderInfo(key, provider.auth_methods, bool(provider.implemented))
            if key == self._selected and self._selected_adapter is not None:
                self._retired_adapters.append(self._selected_adapter)
                self._selected_adapter = None

    def list_providers(self):
        return [{
            "name": p.name, "auth_methods": list(p.auth_methods),
            "implemented": p.implemented and (p.name in self._factories or p.name in {"ollama", "hybrid"}),
            "selected": name == self._selected,
        } for name, p in sorted(self._providers.items())]

    @staticmethod
    def _close_adapter(adapter) -> None:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()

    def _ensure_open(self) -> None:
        if self._closed or self._closing:
            raise RuntimeError("provider manager is closing or closed.")

    def _build_adapter(self, name: str):
        if name in {"ollama", "hybrid"}:
            adapter = create_provider({**self._config, "provider": name}, environ=self._environ)
        else:
            factory = self._factories.get(name)
            if factory is None:
                raise ValueError("Provider is not implemented.")
            adapter = factory()
        if not callable(getattr(adapter, "generate", None)) or not callable(getattr(adapter, "is_available", None)):
            try:
                self._close_adapter(adapter)
            except Exception as cleanup_error:
                raise RuntimeError("invalid provider adapter cleanup failed.") from cleanup_error
            raise TypeError("Provider factory must return an adapter with generate() and is_available().")
        return adapter

    def select(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            return "Provide a provider name."
        key = name.strip().lower()
        # Serialize factory construction with selection publication and close().
        # Otherwise two slow factories can complete out of order and an older
        # request can overwrite a newer selection after it finishes last.
        with self._lifecycle_lock:
            self._ensure_open()
            if key in {"none", "native", "native-only"}:
                if self._selected_adapter is not None:
                    self._retired_adapters.append(self._selected_adapter)
                    self._selected_adapter = None
                self._selected = None
                return "Selected native-only mode; no external model provider is configured."
            info = self._providers.get(key)
            if info is None:
                return f"Unknown provider: {key}"
            if not info.implemented or (key not in self._factories and key not in {"ollama", "hybrid"}):
                return f"{key} is registered but not implemented. Yuki will keep using its current provider."
            try:
                adapter = self._build_adapter(key)
            except Exception as exc:
                return f"Cannot select {key}: provider configuration or construction failed ({type(exc).__name__})."
            if self._selected_adapter is not None:
                self._retired_adapters.append(self._selected_adapter)
            self._selected = key
            self._selected_adapter = adapter
            return f"Selected provider: {key}"

    def create_selected_adapter(self) -> Optional[BaseProvider]:
        with self._lifecycle_lock:
            self._ensure_open()
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
        adapter = ProviderAgentAdapter(provider)
        adapter.provider_manager = self
        return adapter

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
        except Exception as availability_error:
            try:
                self._close_adapter(adapter)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"Routed provider '{selected.provider}' failed availability and cleanup."
                ) from cleanup_error
            raise RuntimeError(
                f"Routed provider '{selected.provider}' is not available; no fallback was attempted."
            ) from availability_error
        if available is not True:
            try:
                self._close_adapter(adapter)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"Routed provider '{selected.provider}' failed availability and cleanup."
                ) from cleanup_error
            raise RuntimeError(
                f"Routed provider '{selected.provider}' is not available; no fallback was attempted."
            )
        with self._lifecycle_lock:
            if self._closed or self._closing:
                try:
                    self._close_adapter(adapter)
                finally:
                    raise RuntimeError("provider manager is closed.")
            self._routed_adapters[id(adapter)] = adapter
        return adapter

    @contextmanager
    def lease_adapter(self, adapter):
        """Pin an exact manager-owned adapter until a provider operation finishes."""
        with self._lifecycle_lock:
            self._ensure_open()
            owned = (
                self._selected_adapter is adapter
                or any(item is adapter for item in self._retired_adapters)
                or self._routed_adapters.get(id(adapter)) is adapter
            )
            if not owned:
                raise ValueError("provider adapter is not owned by this manager.")
            key = id(adapter)
            self._active_leases[key] = self._active_leases.get(key, 0) + 1
        try:
            yield adapter
        finally:
            with self._lifecycle_lock:
                remaining = self._active_leases.get(key, 0) - 1
                if remaining > 0:
                    self._active_leases[key] = remaining
                else:
                    self._active_leases.pop(key, None)

    def release_routed_adapter(self, adapter) -> None:
        """Close one routed adapter created and still owned by this manager.

        Identity checks prevent callers from closing arbitrary or selected
        shared adapters through this lifecycle method. A failed close leaves
        the adapter registered so its owner can retry cleanup.
        """
        with self._lifecycle_lock:
            owned = self._routed_adapters.get(id(adapter))
            if owned is not adapter:
                raise ValueError("routed adapter is not owned by this provider manager.")
            if self._active_leases.get(id(adapter), 0):
                raise RuntimeError("routed adapter is in use; drain its requests before release.")
            self._close_adapter(adapter)
            del self._routed_adapters[id(adapter)]

    def close(self) -> None:
        """Close manager-owned adapters after all request leases have drained."""
        errors = []
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closing = True
            active = sum(self._active_leases.values())
            if active:
                raise RuntimeError(
                    f"provider manager has {active} active adapter lease(s); drain requests and retry close()."
                )
            candidates = []
            if self._selected_adapter is not None:
                candidates.append(self._selected_adapter)
            candidates.extend(self._retired_adapters)
            candidates.extend(self._routed_adapters.values())
            seen = set()
            for adapter in candidates:
                if id(adapter) in seen:
                    continue
                seen.add(id(adapter))
                try:
                    self._close_adapter(adapter)
                except Exception as exc:
                    errors.append(exc)
                    continue
                if self._selected_adapter is adapter:
                    self._selected_adapter = None
                self._retired_adapters = [
                    item for item in self._retired_adapters if item is not adapter
                ]
                self._routed_adapters.pop(id(adapter), None)
            if not errors:
                self._closed = True
        if errors:
            raise RuntimeError(
                f"provider manager could not close {len(errors)} adapter(s); call close() again to retry."
            ) from errors[0]
