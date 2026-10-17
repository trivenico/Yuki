"""Consent-first local identity directory. Stores labels only, never biometric templates."""
from __future__ import annotations
import json
import os
import tempfile
import uuid
from pathlib import Path
from threading import RLock

class IdentityManager:
    MAX_IDENTITIES = 100
    def __init__(self, path="yuki_identities.json"):
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()
    def _load(self):
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Identity store unreadable; no data was overwritten.") from exc
        if not isinstance(items, list) or len(items) > self.MAX_IDENTITIES:
            raise RuntimeError("Invalid identity store.")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("name"), str) or item.get("consent") is not True:
                raise RuntimeError("Invalid identity record.")
        return items
    def list(self):
        with self._lock:
            return [{"id": x["id"], "name": x["name"], "consent": True, "biometric_data_stored": False} for x in self._load()]
    def add(self, name, *, consent=False):
        if not isinstance(name, str):
            raise ValueError("Name must be text.")
        name = " ".join(name.split())
        if not name or len(name) > 100:
            raise ValueError("Name must contain 1-100 characters.")
        if consent is not True:
            raise PermissionError("Explicit consent is required to save an identity.")
        with self._lock:
            items = self._load()
            if len(items) >= self.MAX_IDENTITIES:
                raise ValueError("Identity limit reached.")
            if any(x["name"].casefold() == name.casefold() for x in items):
                raise ValueError("Identity already exists.")
            item = {"id": uuid.uuid4().hex, "name": name, "consent": True}
            items.append(item)
            self._save(items)
            return {"id": item["id"], "name": name, "consent": True, "biometric_data_stored": False}
    def delete(self, identity_id):
        if not isinstance(identity_id, str) or not identity_id:
            return False
        with self._lock:
            items = self._load()
            kept = [x for x in items if x["id"] != identity_id]
            if len(kept) == len(items):
                return False
            self._save(kept)
            return True
    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".yuki-identities-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(items, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
