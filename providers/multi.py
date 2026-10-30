"""Explicit per-task routing across local, OCI-managed, and self-hosted models."""
from __future__ import annotations


class MultiProviderRouter:
    """Route named task classes to configured providers without silent fallback."""

    def __init__(self, providers, *, routes=None, default_route=None):
        if not isinstance(providers, dict) or not providers:
            raise ValueError("At least one named provider is required.")
        normalized = {}
        for name, provider in providers.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Provider names must be non-empty strings.")
            if not callable(getattr(provider, "generate", None)) or not callable(
                getattr(provider, "is_available", None)
            ):
                raise TypeError(f"Provider {name!r} must implement generate() and is_available().")
            normalized[name.strip().lower()] = provider
        if not isinstance(default_route, str) or default_route.strip().lower() not in normalized:
            raise ValueError("default_route must name a configured provider.")
        if routes is None:
            routes = {}
        if not isinstance(routes, dict):
            raise TypeError("routes must be a mapping of task names to provider names.")
        normalized_routes = {}
        for task, provider_name in routes.items():
            if not isinstance(task, str) or not task.strip():
                raise ValueError("Task route names must be non-empty strings.")
            if not isinstance(provider_name, str) or provider_name.strip().lower() not in normalized:
                raise ValueError(f"Task route {task!r} references an unconfigured provider.")
            normalized_routes[task.strip().lower()] = provider_name.strip().lower()
        self.providers = normalized
        self.routes = normalized_routes
        self.default_route = default_route.strip().lower()

    def provider_for(self, task):
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A task name is required.")
        return self.providers[self.routes.get(task.strip().lower(), self.default_route)]

    def is_available(self):
        try:
            return self.providers[self.default_route].is_available() is True
        except Exception:
            return False

    def generate(self, prompt):
        return self.generate_for("general", prompt)

    def generate_for(self, task, prompt):
        provider = self.provider_for(task)
        try:
            available = provider.is_available() is True
        except Exception as exc:
            raise RuntimeError("Selected model provider is not available; fallback is disabled.") from exc
        if not available:
            raise RuntimeError("Selected model provider is not available; fallback is disabled.")
        return provider.generate(prompt)
