"""Consent-first local identity directory; stores labels, never biometric templates."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from companion.state_store import CompanionStateStore


class IdentityManager:
    MAX_IDENTITIES = 100

    def __init__(self, path="yuki_companion.sqlite3", *, owner_id: str = "local",
                 state_store: CompanionStateStore | None = None):
        self.store = state_store or CompanionStateStore(path, owner_id=owner_id)

    def list(self):
        return [{"id": x["id"], "name": x["name"], "consent": True, "biometric_data_stored": False}
                for x in self.store.list_records("identity")]

    def add(self, name, *, consent=False):
        if not isinstance(name, str):
            raise ValueError("Name must be text.")
        name = " ".join(name.split())
        if not name or len(name) > 100:
            raise ValueError("Name must contain 1-100 characters.")
        if consent is not True:
            raise PermissionError("Explicit consent is required to save an identity.")
        if any(x["name"].casefold() == name.casefold() for x in self.store.list_records("identity")):
            raise ValueError("Identity already exists.")
        item = {"id": uuid4().hex, "name": name, "consent": True}
        self.store.add_record("identity", item)
        return {"id": item["id"], "name": name, "consent": True, "biometric_data_stored": False}

    def delete(self, identity_id):
        return self.store.delete_record("identity", identity_id)
