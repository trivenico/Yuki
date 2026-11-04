"""Stable, opaque identities for authenticated owner-scoped local data."""
from __future__ import annotations

import hashlib


def memory_actor_id_for_owner(owner_id: str) -> str:
    """Map a server-authenticated owner ID to a store-safe opaque key."""
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
        raise ValueError("invalid_authenticated_owner_id")
    return "owner-" + hashlib.sha256(owner_id.strip().encode("utf-8")).hexdigest()
