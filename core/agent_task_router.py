"""Advisory bridge from user task classification to specialist/model candidates.

Routing is deliberately side-effect free: it never submits a plan, starts a
worker, activates a model, or grants permissions. The caller must obtain
approval and use the existing coordinator/runtime APIs for execution.
"""
from __future__ import annotations

from core.model_capabilities import (
    ModelCapabilityRegistry,
    classify_task,
    recommend_capable_models,
)

_MAX_TASK_CHARS = 50_000


def recommend_task_route(
    task_text: str,
    *,
    actor_id: str,
    coordinator,
    model_registry: ModelCapabilityRegistry | None = None,
    explicit_task_type: str | None = None,
) -> dict:
    """Return explainable specialist and model candidates for a bounded task."""
    if not isinstance(task_text, str) or not task_text.strip() or len(task_text) > _MAX_TASK_CHARS:
        raise ValueError("task_text_must_be_nonempty_and_bounded")
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
        raise ValueError("actor_id_must_be_nonempty_and_bounded")
    if not callable(getattr(coordinator, "list_agents", None)):
        raise TypeError("coordinator must expose authorized list_agents()")
    classification = classify_task(task_text, explicit_task_type=explicit_task_type)
    required = set(classification["required_capabilities"])
    agents = coordinator.list_agents(actor_id=actor_id.strip())
    if not isinstance(agents, (list, tuple)):
        raise TypeError("coordinator returned an invalid agent registry")
    agent_candidates = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = agent.get("agent_id")
        capabilities = agent.get("capabilities")
        if (
            agent.get("enabled") is True
            and isinstance(agent_id, str)
            and isinstance(capabilities, (list, tuple))
            and required.issubset(set(capabilities))
        ):
            agent_candidates.append({
                "agent_id": agent_id,
                "name": agent.get("name", agent_id),
                "capabilities": sorted(set(capabilities)),
            })
    agent_candidates.sort(key=lambda item: item["agent_id"])
    model_recommendations = None
    if model_registry is not None:
        model_recommendations = recommend_capable_models(classification, model_registry)
    return {
        "classification": classification,
        "specialist_candidates": agent_candidates,
        "model_recommendations": model_recommendations,
        "needs_clarification": classification["needs_clarification"],
        "requires_user_approval": True,
        "dispatch_performed": False,
        "model_activated": False,
        "next_action": (
            "clarify_task" if classification["needs_clarification"]
            else "review_candidates_and_approve"
        ),
    }
