"""Bounded local working memory and JSON-backed knowledge graph."""
from __future__ import annotations
import json
from collections import deque
from pathlib import Path
from threading import RLock
from typing import Any

class WorkingMemory:
    def __init__(self, max_turns: int = 20) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self._turns: deque[dict[str, str]] = deque(maxlen=max_turns)
    def add(self, role: str, content: str) -> None:
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("unsupported role")
        self._turns.append({"role": role, "content": content[:20_000]})
    def messages(self) -> list[dict[str, str]]:
        return list(self._turns)

class KnowledgeGraph:
    def __init__(self, path: str | Path = "data/knowledge_graph.json") -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._data: dict[str, Any] = {"entities": {}, "relations": []}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and isinstance(value.get("entities"), dict) and isinstance(value.get("relations"), list):
                    self._data = value
            except (OSError, json.JSONDecodeError):
                pass  # Keep corrupt source untouched for recovery.
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)
    def add_entity(self, entity_id: str, label: str, kind: str = "concept") -> None:
        if not entity_id.strip() or len(entity_id) > 300:
            raise ValueError("invalid entity id")
        with self._lock:
            self._data["entities"][entity_id] = {"label": label[:500], "kind": kind[:100]}
            self._save()
    def add_relation(self, source: str, relation: str, target: str) -> None:
        with self._lock:
            if source not in self._data["entities"] or target not in self._data["entities"]:
                raise KeyError("both relation endpoints must exist")
            row = {"source": source, "relation": relation[:200], "target": target}
            if row not in self._data["relations"]:
                self._data["relations"].append(row)
                self._save()
    def query_relations(self, entity_id: str) -> list[dict[str, str]]:
        with self._lock:
            return [r.copy() for r in self._data["relations"] if r["source"] == entity_id or r["target"] == entity_id]
    def export_obsidian(self, directory: str | Path) -> list[Path]:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []
        with self._lock:
            entities = list(self._data["entities"].items())
        for entity_id, entity in entities:
            lines = [f"# {entity['label']}", "", f"- ID: {entity_id}", f"- Type: {entity['kind']}", "", "## Relations"]
            for row in self.query_relations(entity_id):
                lines.append(f"- {row['source']} — {row['relation']} — {row['target']}")
            filename = entity_id.replace("/", "_").replace("\\", "_") + ".md"
            path = target / filename
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            out.append(path)
        return out
