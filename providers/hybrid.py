"""Explicit per-task hybrid routing with local-first privacy defaults."""
from __future__ import annotations


class HybridProvider:
    """Route only explicitly named cloud tasks to cloud; all other work stays local.

    This router only generates text. It never executes tools or actions. A missing
    local provider never triggers an implicit cloud fallback.
    """

    def __init__(self, local_provider, cloud_provider=None, *, cloud_tasks=(), owns_providers=False):
        if not callable(getattr(local_provider, "generate", None)) or not callable(
            getattr(local_provider, "is_available", None)
        ):
            raise TypeError("A compatible local provider is required.")
        if cloud_provider is not None and (
            not callable(getattr(cloud_provider, "generate", None))
            or not callable(getattr(cloud_provider, "is_available", None))
        ):
            raise TypeError("Cloud provider must implement generate() and is_available().")
        if isinstance(cloud_tasks, str):
            raise TypeError("cloud_tasks must be an iterable of task names, not a string.")
        normalized = set()
        for task in cloud_tasks:
            if not isinstance(task, str) or not task.strip():
                raise ValueError("Cloud task names must be non-empty strings.")
            normalized.add(task.strip().lower())
        self.local_provider = local_provider
        self.cloud_provider = cloud_provider
        if type(owns_providers) is not bool:
            raise TypeError("owns_providers must be a boolean.")
        self.cloud_tasks = frozenset(normalized)
        self._owns_providers = owns_providers
        self._closed = False

    def is_available(self):
        try:
            return self.local_provider.is_available() is True
        except Exception:
            return False

    def generate_chat(self, system: str, messages: list[dict]) -> str:
        """Use structured roles on the local provider; never flatten system prompts."""
        chat = getattr(self.local_provider, "generate_chat", None)
        if not callable(chat):
            raise RuntimeError("Configured local provider does not support structured chat.")
        try:
            if self.local_provider.is_available() is not True:
                raise RuntimeError("Local provider is not available; cloud fallback is disabled.")
        except Exception as exc:
            raise RuntimeError("Local provider is not available; cloud fallback is disabled.") from exc
        return chat(system, messages)

    def generate(self, prompt: str) -> str:
        """Default route is local; callers must opt in to a named cloud task."""
        return self.generate_for("general", prompt)

    def generate_for(self, task: str, prompt: str) -> str:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A task name is required.")
        task_key = task.strip().lower()
        if task_key in self.cloud_tasks:
            if self.cloud_provider is None:
                raise RuntimeError("This task is cloud-routed but no cloud provider is configured.")
            try:
                if self.cloud_provider.is_available() is not True:
                    raise RuntimeError("Cloud provider is not available.")
            except Exception as exc:
                raise RuntimeError("Cloud provider is not available.") from exc
            return self.cloud_provider.generate(prompt)
        try:
            if self.local_provider.is_available() is not True:
                raise RuntimeError("Local provider is not available; cloud fallback is disabled.")
        except Exception as exc:
            raise RuntimeError("Local provider is not available; cloud fallback is disabled.") from exc
        return self.local_provider.generate(prompt)

    def close(self):
        """Close owned child providers once, without closing borrowed instances."""
        if self._closed:
            return
        self._closed = True
        if not self._owns_providers:
            return
        first_error = None
        seen = set()
        # Close in reverse construction order and continue after individual failures.
        for provider in (self.cloud_provider, self.local_provider):
            if provider is None or id(provider) in seen:
                continue
            seen.add(id(provider))
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise RuntimeError("One or more owned providers failed to close.") from first_error
