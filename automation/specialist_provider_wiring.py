"""Bridge Yuki's reviewed specialist catalog to provider-backed text handlers.

Role assignment is explicit configuration: display names and capability labels
never implicitly choose a model prompt or grant tool access.
"""
from __future__ import annotations

from typing import Mapping

from automation.specialist_agent_catalog import default_specialist_agents
from automation.specialist_worker import build_handlers_from_provider_manager

_SUPPORTED_ROLES = frozenset({"research", "coding", "testing", "reporting"})


class SpecialistProviderWiringError(ValueError):
    """The specialist catalog cannot be safely wired to configured providers."""


def build_catalog_provider_handlers(
    provider_manager,
    *,
    role_map: Mapping[str, str],
    provider_preferences: Mapping[str, tuple[str, ...]] | None = None,
    allow_cloud: bool = False,
) -> dict:
    """Create text-only handlers for an explicit subset of catalog specialists.

    The caller must explicitly map each enabled catalog agent ID to one of the
    existing worker's four reviewed prompt roles. No heuristic role selection
    or provider fallback is performed. Cloud routing remains opt-in.
    """
    if not isinstance(role_map, Mapping) or not role_map or len(role_map) > 12:
        raise SpecialistProviderWiringError("role_map must explicitly configure 1-12 specialists.")
    catalog = {agent.agent_id: agent for agent in default_specialist_agents()}
    normalized = {}
    for agent_id, role in role_map.items():
        if not isinstance(agent_id, str) or agent_id not in catalog:
            raise SpecialistProviderWiringError("role_map references an unknown catalog specialist.")
        if not catalog[agent_id].enabled:
            raise SpecialistProviderWiringError("disabled specialists cannot receive provider handlers.")
        if not isinstance(role, str) or role not in _SUPPORTED_ROLES:
            raise SpecialistProviderWiringError("each specialist must map to a supported reviewed worker role.")
        normalized[agent_id] = role
    try:
        return build_handlers_from_provider_manager(
            provider_manager,
            normalized,
            provider_preferences=provider_preferences,
            allow_cloud=allow_cloud,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise SpecialistProviderWiringError("specialist provider handler construction failed.") from exc
