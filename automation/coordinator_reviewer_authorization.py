"""Coordinator-backed reviewer authorization for verification evidence.

Only enabled, registered specialists with an explicit permission and a
currently authorized coordinator principal may author or review a report.
"""
from __future__ import annotations

from typing import Any


def coordinator_reviewer_authorizer(coordinator: Any):
    """Build a fail-closed VerificationEvidenceStore authorization callback.

    The coordinator's trusted authorization policy is consulted on every call;
    this adapter does not accept identities or grants from report payloads.
    """
    if coordinator is None or not isinstance(getattr(coordinator, "agents", None), dict):
        raise TypeError("a configured specialist coordinator is required")
    allowed_actions = {
        "verification:submit": "verification:submit",
        "verification:review": "verification:review",
    }

    def authorize(agent_id: str, action: str, artifact_sha256: str) -> bool:
        permission = allowed_actions.get(action)
        if permission is None:
            return False
        if not isinstance(agent_id, str) or not agent_id.strip():
            return False
        if not isinstance(artifact_sha256, str) or len(artifact_sha256) != 64:
            return False
        agent = coordinator.agents.get(agent_id)
        if agent is None or getattr(agent, "enabled", False) is not True:
            return False
        permissions = getattr(agent, "permissions", frozenset())
        if permission not in permissions:
            return False
        try:
            return coordinator._allowed(agent_id, action, artifact_sha256) is True
        except Exception:
            return False

    return authorize
