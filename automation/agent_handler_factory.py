"""Build provider-backed handlers directly from persisted agent specifications.

This adapter deliberately delegates provider selection to Yuki's existing
provider manager and specialist-worker policy rather than creating a second
provider-routing implementation.
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
    """Create bounded specialist handlers using each registered agent's model policy.

    Agents are grouped by provider preferences and cloud-consent setting so the
    existing provider manager can share compatible adapters. Each group is kept
    within the worker builder's 32-agent input limit.
    """
    if not isinstance(registry, SQLiteAgentRegistry):
        raise TypeError("registry must be a SQLiteAgentRegistry.")

    groups = defaultdict(list)
    for spec in registry.list_agents():
        groups[(spec.provider_preferences, spec.allow_cloud)].append(spec)

    handlers: dict[str, Callable[[str], str]] = {}
    for (preferences, allow_cloud), specs in groups.items():
        for offset in range(0, len(specs), 32):
            batch = specs[offset:offset + 32]
            roles = {spec.agent_id: spec.role for spec in batch}
            preference_map = {
                spec.agent_id: spec.provider_preferences
                for spec in batch if spec.provider_preferences
            }
            handlers.update(build_handlers_from_provider_manager(
                provider_manager,
                roles,
                provider_preferences=preference_map,
                allow_cloud=allow_cloud,
            ))
    return handlers
