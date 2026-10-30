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
    *,
    cloud_consent_agent_ids=(),
) -> dict[str, Callable[[str], str]]:
    """Create handlers using each registered agent's model and privacy policy.

    Agents are grouped by provider preference and cloud-consent settings. An
    agent without an explicit preference is pinned to the provider currently
    selected by the user; if no selected provider is known, construction fails
    closed. The router enforces cloud consent and never searches/falls back
    across providers. Groups are processed in bounded application batches without
    imposing a product-level cap on the total registered fleet.
    """
    if not isinstance(registry, SQLiteAgentRegistry):
        raise TypeError("registry must be a SQLiteAgentRegistry.")

    current_factory = getattr(provider_manager, "current", None)
    current_info = current_factory() if callable(current_factory) else None
    selected_name = getattr(current_info, "name", None)

    if isinstance(cloud_consent_agent_ids, (str, bytes)):
        raise TypeError("cloud_consent_agent_ids must be an iterable of agent IDs, not text.")
    try:
        consent_ids = frozenset(cloud_consent_agent_ids)
    except TypeError as exc:
        raise TypeError("cloud_consent_agent_ids must be iterable.") from exc
    if any(not isinstance(agent_id, str) or not agent_id.strip() for agent_id in consent_ids):
        raise ValueError("cloud consent entries must be non-empty agent IDs.")
    registered_specs = registry.list_agents()
    registered_ids = {spec.agent_id for spec in registered_specs}
    if not consent_ids.issubset(registered_ids):
        raise ValueError("cloud consent references an unregistered agent ID.")

    groups = defaultdict(list)
    for spec in registered_specs:
        preferences = spec.provider_preferences
        if not preferences:
            if not isinstance(selected_name, str) or not selected_name.strip():
                raise RuntimeError(
                    f"agent {spec.agent_id!r} has no explicit provider preference and no selected provider is available."
                )
            preferences = (selected_name.strip().lower(),)
        groups[(preferences, spec.allow_cloud or spec.agent_id in consent_ids)].append(spec)

    handlers: dict[str, Callable[[str], str]] = {}
    for (preferences, allow_cloud), specs in groups.items():
        batch_size = 256
        for offset in range(0, len(specs), batch_size):
            batch = specs[offset:offset + batch_size]
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
