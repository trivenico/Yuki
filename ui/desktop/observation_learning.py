"""Authenticated desktop composition for observation-learning review.

The desktop receives only the authorized service. The SQLite store remains
owner-scoped and is never passed to widgets.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.auth import BuiltInAuthService, observation_review_authorizer
from core.observation_knowledge_store import SQLiteObservationKnowledgeReview
from core.observation_review_control import ObservationKnowledgeReviewService
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity
from ui.desktop.auth import DesktopSessionManager


def build_desktop_observation_review_service(
    auth: BuiltInAuthService,
    sessions: DesktopSessionManager,
    identity: AuthenticatedDesktopIdentity,
    database_path: str | Path,
    *,
    capacity: int = 512,
    retention_seconds: int = 30 * 24 * 60 * 60,
) -> ObservationKnowledgeReviewService:
    """Compose review access only for the currently authenticated desktop user."""
    if not isinstance(auth, BuiltInAuthService):
        raise TypeError("auth must be a BuiltInAuthService")
    if not isinstance(sessions, DesktopSessionManager):
        raise TypeError("sessions must be a DesktopSessionManager")
    if sessions.auth_service is not auth:
        raise ValueError("sessions and auth must share the same authentication service")
    if not isinstance(identity, AuthenticatedDesktopIdentity):
        raise TypeError("identity must be an AuthenticatedDesktopIdentity")
    current = sessions.current_identity()
    if (
        current is None
        or current.actor_id != identity.actor_id
        or current.session_id != identity.session_id
    ):
        raise PermissionError("an active matching desktop session is required")

    store = SQLiteObservationKnowledgeReview(
        str(database_path),
        owner_id=identity.actor_id,
        capacity=capacity,
        retention_seconds=retention_seconds,
    )
    authorize = observation_review_authorizer(
        auth,
        session_token_provider=lambda: (
            current_identity.session_id
            if (current_identity := sessions.current_identity()) is not None
            else ""
        ),
    )
    return ObservationKnowledgeReviewService(store, authorize=authorize)


__all__ = ["build_desktop_observation_review_service"]
