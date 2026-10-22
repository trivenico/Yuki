"""Policy-first task router for explicitly configured provider adapters."""
from __future__ import annotations
import re
from collections.abc import Mapping

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

def _name(value, label):
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    normalized = value.strip().lower()
    if not _NAME.fullmatch(normalized):
        raise ValueError(f"{label} must be a simple provider/task identifier.")
    return normalized

class TaskRouter:
    """Route explicit tasks to injected providers; never silently fall back."""
    def __init__(self, providers: Mapping[str, object], *, default_provider: str, task_routes=None):
        if not isinstance(providers, Mapping) or not providers:
            raise ValueError("At least one configured provider is required.")
        normalized = {}
        for key, provider in providers.items():
            name = _name(key, "Provider name")
            if name in normalized:
                raise ValueError("Provider names must be unique after normalization.")
            if not callable(getattr(provider, "generate", None)) or not callable(getattr(provider, "is_available", None)):
                raise TypeError(f"Provider {name!r} must implement generate() and is_available().")
            normalized[name] = provider
        default_name = _name(default_provider, "Default provider")
        if default_name not in normalized:
            raise ValueError("Default provider is not configured.")
        routes = {}
        for task, provider_name in (task_routes or {}).items():
            task_key = _name(task, "Task name")
            provider_key = _name(provider_name, "Routed provider")
            if task_key in routes:
                raise ValueError("Task routes must be unique after normalization.")
            if provider_key not in normalized:
                raise ValueError(f"Task {task_key!r} references an unconfigured provider.")
            routes[task_key] = provider_key
        self.providers = dict(normalized)
        self.default_provider = default_name
        self.task_routes = dict(routes)

    def provider_for(self, task: str) -> str:
        return self.task_routes.get(_name(task, "Task name"), self.default_provider)

    def is_available(self) -> bool:
        try:
            return self.providers[self.default_provider].is_available() is True
        except Exception:
            return False

    def generate(self, prompt: str) -> str:
        return self.generate_for("general", prompt)

    def generate_for(self, task: str, prompt: str) -> str:
        provider_name = self.provider_for(task)
        provider = self.providers[provider_name]
        try:
            available = provider.is_available()
        except Exception as exc:
            raise RuntimeError(f"Configured provider {provider_name!r} is unavailable.") from exc
        if available is not True:
            raise RuntimeError(f"Configured provider {provider_name!r} is unavailable; implicit fallback is disabled.")
        return provider.generate(prompt)
