"""Task-aware, privacy-first routing over Yuki's existing provider registry.

Profiles express orchestration intent, not undocumented model abilities. The
provider capability registry remains authoritative for readiness and privacy.
Cloud routing is opt-in on every call; no fallback occurs after selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class TaskProfile:
    task: str
    description: str
    required_capabilities: tuple[str, ...]
    provider_preference: tuple[str, ...]


@dataclass(frozen=True)
class RoutedTaskModel:
    task: str
    provider: str
    privacy: str
    configured_model: str | None
    adapter: Any


_DEFAULT_PREFERENCE = ("ollama", "openai", "gemini")
_PROFILES: Mapping[str, TaskProfile] = {
    name: TaskProfile(
        task=name,
        description=description,
        # All current supported adapters are advertised as text-capable.
        # Do not infer coding, vision, tools, or context-window abilities here.
        required_capabilities=("text",),
        provider_preference=_DEFAULT_PREFERENCE,
    )
    for name, description in {
        "conversation": "Natural-language dialogue and companion interaction.",
        "coding": "Software engineering, debugging, and code review.",
        "research": "Research synthesis and evidence organization.",
        "planning": "Task decomposition, scheduling, and execution planning.",
        "analysis": "Reasoning, comparison, and structured analysis.",
    }.items()
}


class TaskModelRouter:
    """Select a configured provider for a task using explicit privacy policy.

    The provider manager is expected to expose routing_registry() and
    create_routed_adapter(). It owns adapter construction and availability
    checks; this class never constructs providers or retries another provider.
    """

    def __init__(self, provider_manager: Any):
        if not callable(getattr(provider_manager, "routing_registry", None)):
            raise TypeError("provider_manager must expose routing_registry().")
        if not callable(getattr(provider_manager, "create_routed_adapter", None)):
            raise TypeError("provider_manager must expose create_routed_adapter().")
        self.provider_manager = provider_manager

    @staticmethod
    def profiles() -> tuple[TaskProfile, ...]:
        return tuple(_PROFILES[name] for name in sorted(_PROFILES))

    def route(
        self,
        task: str,
        *,
        preference: Sequence[str] | None = None,
        allow_cloud: bool = False,
    ) -> RoutedTaskModel:
        if not isinstance(task, str) or task.strip().lower() not in _PROFILES:
            raise ValueError("task must be one of: " + ", ".join(sorted(_PROFILES)))
        if type(allow_cloud) is not bool:
            raise TypeError("allow_cloud must be a boolean; cloud routing is explicit.")
        profile = _PROFILES[task.strip().lower()]
        if preference is None:
            ordered = profile.provider_preference
        else:
            if isinstance(preference, (str, bytes)):
                raise TypeError("preference must be a sequence of provider names.")
            if not isinstance(preference, (list, tuple)) or not preference or len(preference) > 16:
                raise ValueError("preference must contain between 1 and 16 provider names.")
            if any(not isinstance(item, str) or not item.strip() for item in preference):
                raise ValueError("provider preferences must be non-empty strings.")
            ordered = tuple(item.strip().lower() for item in preference)

        # Resolve against sanitized, readiness-aware metadata first. Strict
        # preference prevents an unrequested adapter from being substituted.
        registry = self.provider_manager.routing_registry()
        selected = registry.route(
            required=profile.required_capabilities,
            preference=ordered,
            allow_cloud=allow_cloud,
            strict_preference=True,
        )
        # Pin adapter creation to the selected provider. The manager performs
        # a live availability check and raises rather than silently falling back.
        adapter = self.provider_manager.create_routed_adapter(
            required=profile.required_capabilities,
            preference=(selected.provider,),
            allow_cloud=allow_cloud,
        )
        return RoutedTaskModel(
            task=profile.task,
            provider=selected.provider,
            privacy=selected.privacy,
            configured_model=selected.configured_model,
            adapter=adapter,
        )
