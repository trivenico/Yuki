"""Lazy desktop provider adapter shared by legacy and durable chat paths.

Provider construction is deferred until the first inference request. The adapter
preserves both existing desktop call contracts without enabling tools or
implicitly selecting a fallback provider.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Mapping


class LazyDesktopProvider:
    """Thread-safe, single-initialization adapter for desktop chat providers."""

    def __init__(
        self,
        config: dict[str, Any],
        provider_factory: Callable[..., Any],
    ) -> None:
        if not isinstance(config, dict):
            raise TypeError("config must be a dictionary.")
        if not callable(provider_factory):
            raise TypeError("provider_factory must be callable.")
        self._config = dict(config)
        self._provider_factory = provider_factory
        self._provider: Any = None
        self._initialization_failed = False
        self._closed = False
        self._lock = threading.RLock()

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._provider is not None

    def _get_provider(self) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError("desktop_provider_closed")
            if self._provider is not None:
                return self._provider
            if self._initialization_failed:
                raise RuntimeError("configured_provider_unavailable")
            try:
                provider = self._provider_factory(dict(self._config))
                if provider is None or not callable(getattr(provider, "generate_chat", None)):
                    raise TypeError("provider_does_not_support_chat")
            except Exception:
                self._initialization_failed = True
                raise RuntimeError("configured_provider_unavailable") from None
            self._provider = provider
            return provider

    def generate_chat(self, *args: Any, **kwargs: Any) -> Any:
        """Support structured durable chat and legacy (system, messages) chat."""
        provider = self._get_provider()
        if kwargs:
            if args:
                raise TypeError("structured chat does not accept mixed arguments.")
            return provider.generate_chat(**kwargs)
        if len(args) != 2:
            raise TypeError("legacy chat requires system text and messages.")
        system, messages = args
        if not isinstance(system, str) or not isinstance(messages, (list, tuple)):
            raise TypeError("legacy chat input is invalid.")
        transcript = [{"role": "system", "content": system}]
        for item in messages:
            if not isinstance(item, Mapping) or set(item) != {"role", "content"}:
                raise ValueError("legacy chat message is invalid.")
            transcript.append({"role": item["role"], "content": item["content"]})
        response = provider.generate_chat(
            messages=transcript, tools=[], tool_choice="none",
        )
        if not isinstance(response, Mapping):
            raise RuntimeError("configured_provider_returned_invalid_response")
        answer = response.get("text")
        if not isinstance(answer, str):
            raise RuntimeError("configured_provider_returned_invalid_response")
        return answer

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            provider = self._provider
            close = getattr(provider, "close", None)
            if callable(close):
                # Retain the provider and leave the adapter open if cleanup fails.
                # DesktopServiceLifecycle can then retry this exact resource.
                close()
            self._provider = None
            self._closed = True
