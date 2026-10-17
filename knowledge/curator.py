"""Provenance-aware curation for explicitly supplied knowledge items."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading


@dataclass(frozen=True)
class KnowledgeItem:
    item_id: str
    title: str
    content: str
    source: str
    trust: str
    created_at: str
    approved: bool = False


class KnowledgeCurator:
    """Stores reviewed items with provenance; ingestion does not grant trust."""

    TRUST_LEVELS = {"untrusted", "reviewed", "trusted"}

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else None
        self._lock = threading.RLock()
        self._items: dict[str, KnowledgeItem] = {}
        self._load()

    def _load(self):
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list) or len(raw) > 10000:
                raise ValueError("invalid knowledge store")
            for row in raw:
                item = KnowledgeItem(**row)
                if item.trust not in self.TRUST_LEVELS:
                    raise ValueError("invalid trust label")
                self._items[item.item_id] = item
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Knowledge store is invalid; original data was not overwritten.") from exc

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([item.__dict__ for item in self._items.values()],
                                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def add(self, *, title: str, content: str, source: str, trust: str = "untrusted") -> KnowledgeItem:
        if trust not in self.TRUST_LEVELS:
            raise ValueError("trust must be untrusted, reviewed, or trusted")
        if not all(isinstance(v, str) and v.strip() for v in (title, content, source)):
            raise ValueError("title, content, and source are required")
        if len(title) > 300 or len(content) > 200000 or len(source) > 2000:
            raise ValueError("knowledge item exceeds size limits")
        digest = hashlib.sha256((source + "\0" + title + "\0" + content).encode("utf-8")).hexdigest()
        item = KnowledgeItem(digest[:24], title.strip(), content, source.strip(), trust,
                             datetime.now(timezone.utc).isoformat(), False)
        with self._lock:
            self._items[item.item_id] = item
            self._save()
        return item

    def approve(self, item_id: str, *, reviewer: str) -> KnowledgeItem:
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("reviewer is required")
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError("knowledge item not found")
            approved = KnowledgeItem(**{**item.__dict__, "approved": True})
            self._items[item_id] = approved
            self._save()
            return approved

    def list_items(self, *, approved_only: bool = False) -> list[KnowledgeItem]:
        with self._lock:
            items = list(self._items.values())
        if approved_only:
            items = [item for item in items if item.approved]
        return sorted(items, key=lambda item: item.created_at)
