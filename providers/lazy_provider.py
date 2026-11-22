"""Lazy, single-instance provider construction for the API runtime."""
from __future__ import annotations

import threading
from typing import Any, Callable, Mapping


class LazyProvider:
    """Defer configured provider construction until the first model operation.

    Initialization is serialized and failures are cached for the lifetime of
    this adapter. The adapter never substitutes a mock or another provider.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        environ: Mapping[str, str],
        provider_factory: Callable[..., Any],
    ):
        if not isinstance(config, dict):
            raise TypeError("config must be a dictionary")
        if not isinstance(environ, Mapping):
            raise TypeError("environ must be a mapping")
        if not callable(provider_factory):
            raise TypeError("provider_factory must be callable")
        self._config = config
        self._environ = environ
        self._provider_factory = provider_factory
        self._provider: Any | None = None
        self._failure: Exception | None = None
        self._closed = False
        self._lock = threading.RLock()

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._provider is not None

    def _get_provider(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("configured_provider_closed")
            if self._provider is not None:
                return self._provider
            if self._failure is not None:
                raise RuntimeError("configured_provider_unavailable") from self._failure
            try:
                provider = self._provider_factory(self._config, environ=self._environ)
                if provider is None or not callable(getattr(provider, "generate_chat", None)):
                    raise TypeError("provider does not implement generate_chat")
            except Exception as exc:
                self._failure = exc
                raise RuntimeError("configured_provider_unavailable") from exc
            self._provider = provider
            return provider

    def generate_chat(self, *, messages, tools, tool_choice="auto"):
        return self._get_provider().generate_chat(
            messages=messages, tools=tools, tool_choice=tool_choice,
        )

    def generate_chat_stream(self, *, messages, tools, tool_choice="auto"):
        provider = self._get_provider()
        stream = getattr(provider, "generate_chat_stream", None)
        if not callable(stream):
            raise RuntimeError("configured provider does not support native streaming")
        return stream(messages=messages, tools=tools, tool_choice=tool_choice)

    def close(self):
        with self._lock:
            if self._closed:
                return
            provider = self._provider
            if provider is not None:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            self._closed = True
            self._provider = None
