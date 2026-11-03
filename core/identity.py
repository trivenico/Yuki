"""Verified request identity shared by API, permission gates and durable approvals.

Only construct identities from a BuiltInAuthService-resolved user and the bearer
session token held by the server. Never accept these fields from request JSON.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from core.auth import AuthenticatedUser


@dataclass(frozen=True)
class AuthenticatedIdentity:
    actor_id: str
    session_id: str
    role: str

    @classmethod
    def from_session(cls, user: AuthenticatedUser, token: str) -> "AuthenticatedIdentity":
        if not isinstance(user, AuthenticatedUser):
            raise TypeError("user must be resolved by BuiltInAuthService.")
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise ValueError("A valid bearer session token is required.")
        if user.role not in {"admin", "operator", "user"}:
            raise ValueError("Authenticated user has an invalid role.")
        # Never persist or pass the bearer secret into audit or approval storage.
        session_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return cls(actor_id=user.user_id, session_id=session_id, role=user.role)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
