"""Explicit per-task hybrid routing with local-first privacy defaults."""
from __future__ import annotations


class HybridProvider:
    """Route only explicitly named cloud tasks to cloud; all other work stays local.

    This router only generates text. It never executes tools or actions. A missing
    local provider never triggers an implicit cloud fallback.
    """

    def __init__(self, local_provider, cloud_provider=None, *, cloud_tasks=(), cloud_approver=None):
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
        self.cloud_tasks = frozenset(normalized)
        self.cloud_approver = cloud_approver

    def is_available(self):
        try:
            return self.local_provider.is_available() is True
        except Exception:
            return False

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
            if self.cloud_approver is None or self.cloud_approver(task_key, prompt) is not True:
                raise RuntimeError("External cloud transmission was not explicitly approved.")
            result = self.cloud_provider.generate(prompt)
            self.last_usage = getattr(self.cloud_provider, "last_usage", {})
            self.model = getattr(self.cloud_provider, "model", "")
            return result
        try:
            if self.local_provider.is_available() is not True:
                raise RuntimeError("Local provider is not available; cloud fallback is disabled.")
        except Exception as exc:
            raise RuntimeError("Local provider is not available; cloud fallback is disabled.") from exc
        result = self.local_provider.generate(prompt)
        self.last_usage = getattr(self.local_provider, "last_usage", {})
        self.model = getattr(self.local_provider, "model", "")
        return result
