"""Safe discovery bridge between planned capabilities and registered agents.

Discovery is read-only: it never registers, enables, plans, or dispatches agents.
A catalog capability is considered available only when an explicitly registered,
enabled runtime agent advertises the exact stable capability ID.
"""
from __future__ import annotations

from automation.agent_coordinator import SpecialistAgentCoordinator
from automation.capability_catalog import search_capabilities


def discover_capabilities(
    *,
    coordinator: SpecialistAgentCoordinator,
    actor_id: str,
    query: str,
    limit: int = 10,
) -> tuple[dict[str, object], ...]:
    """Return catalog proposals and exact-ID runtime matches for an authorized actor.

    Registry authorization is delegated to the coordinator and fails closed.
    Planned catalog entries are never promoted or treated as executable.
    """
    if not isinstance(coordinator, SpecialistAgentCoordinator):
        raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
    capabilities = search_capabilities(query, limit=limit)
    if not capabilities:
        return ()

    # This call enforces agent:read authorization and refreshes the persisted
    # registry. No coordinator mutation or task dispatch is performed here.
    agents = coordinator.list_agents(actor_id=actor_id)
    results = []
    for capability in capabilities:
        matches = tuple(
            agent["agent_id"]
            for agent in agents
            if agent["enabled"] and capability.capability_id in agent["capabilities"]
        )
        results.append({
            "capability_id": capability.capability_id,
            "name": capability.name,
            "domain": capability.domain,
            "description": capability.description,
            "status": capability.status,
            "execution_mode": capability.execution_mode,
            "approval_required": capability.approval_required,
            "available": bool(matches),
            "registered_agent_ids": matches,
        })
    return tuple(results)
