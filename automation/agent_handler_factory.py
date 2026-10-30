"""Build provider-backed handlers directly from persisted agent specifications.

This adapter delegates provider selection to Yuki's existing provider manager
and specialist-worker policy rather than creating a second routing layer.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from automation.specialist_worker import build_handlers_from_provider_manager
from automation.sqlite_agent_registry import SQLiteAgentRegistry


def build_agent_handlers(
    provider_manager,
    registry: SQLiteAgentRegistry,
) -> dict[str, Callable[[str], str]]:
    """Create handlers using each registered agent's model and privacy policy.

    Agents are grouped by provider preference and cloud-consent settings. An
    agent that permits cloud use but has no explicit preference is pinned to
    the provider currently selected by the user; it never searches/falls back
    across providers. Each group is batched to the worker builder's 32-agent cap.
    """
    if not isinstance(registry, SQLiteAgentRegistry):
        raise TypeError("registry must be a SQLiteAgentRegistry.")

    current_factory = getattr(provider_manager, "current", None)
    current_info = current_factory() if callable(current_factory) else None
    selected_name = getattr(current_info, "name", None)

    groups = defaultdict(list)
    for spec in registry.list_agents():
        preferences = spec.provider_preferences
        if spec.allow_cloud and not preferences:
            if not isinstance(selected_name, str) or not selected_name.strip():
                raise RuntimeError(
                    f"agent {spec.agent_id!r} allows cloud use but no selected provider is available."
                )
            preferences = (selected_name.strip().lower(),)
        groups[(preferences, spec.allow_cloud)].append(spec)

    handlers: dict[str, Callable[[str], str]] = {}
    for (preferences, allow_cloud), specs in groups.items():
        for offset in range(0, len(specs), 32):
            batch = specs[offset:offset + 32]
            roles = {spec.agent_id: spec.role for spec in batch}
            preference_map = {
                spec.agent_id: preferences
                for spec in batch if preferences
            }
            handlers.update(build_handlers_from_provider_manager(
                provider_manager,
                roles,
                provider_preferences=preference_map,
                allow_cloud=allow_cloud,
            ))
    return handlers
