"""Small local personal knowledge graph with explicit CRUD and Obsidian export."""
from __future__ import annotations
import json, os, tempfile
from pathlib import Path
from threading import RLock
from uuid import uuid4

class KnowledgeGraph:
    MAX_ENTITIES = 5000
    MAX_RELATIONS = 20000
    def __init__(self, path="yuki_knowledge_graph.json"):
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()
    def _load(self):
        try: data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError: return {"entities": [], "relations": []}
        except (OSError, json.JSONDecodeError) as exc: raise RuntimeError("Knowledge graph unreadable; data was not overwritten.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("entities"), list) or not isinstance(data.get("relations"), list):
            raise RuntimeError("Knowledge graph has invalid structure.")
        if len(data["entities"]) > self.MAX_ENTITIES or len(data["relations"]) > self.MAX_RELATIONS: raise RuntimeError("Knowledge graph exceeds limits.")
        ids = {e.get("id") for e in data["entities"] if isinstance(e, dict)}
        if len(ids) != len(data["entities"]) or any(not isinstance(e, dict) or not isinstance(e.get("name"), str) for e in data["entities"]): raise RuntimeError("Knowledge graph entity data is invalid.")
        for r in data["relations"]:
            if not isinstance(r, dict) or r.get("source") not in ids or r.get("target") not in ids: raise RuntimeError("Knowledge graph relation references a missing entity.")
        return data
    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".yuki-graph-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(temp, self.path)
        finally:
            try: os.unlink(temp)
            except FileNotFoundError: pass
    def add_entity(self, name, kind="fact", description=""):
        if not isinstance(name, str) or not name.strip() or len(name) > 200: raise ValueError("Entity name must be 1-200 characters.")
        if kind not in {"person","preference","fact","event","place","project","organization","concept","skill","media"}: raise ValueError("Unsupported entity type.")
        if not isinstance(description, str) or len(description) > 2000: raise ValueError("Description must be at most 2000 characters.")
        with self._lock:
            d=self._load()
            for e in d["entities"]:
                if e["name"].casefold() == name.strip().casefold() and e["kind"] == kind:
                    if description.strip() and len(description) > len(e["description"]): e["description"] = description.strip(); self._save(d)
                    return dict(e)
            if len(d["entities"]) >= self.MAX_ENTITIES: raise ValueError("Knowledge graph is full.")
            item={"id":uuid4().hex,"name":name.strip(),"kind":kind,"description":description.strip()}
            d["entities"].append(item); self._save(d); return dict(item)
    def add_relation(self, source_id, relation, target_id):
        if not isinstance(relation,str) or not relation.strip() or len(relation)>80 or not relation.replace("_","").replace("-","").isalnum(): raise ValueError("Relation must be a short identifier.")
        with self._lock:
            d=self._load(); ids={e["id"] for e in d["entities"]}
            if source_id not in ids or target_id not in ids: raise ValueError("Both relation endpoints must exist.")
            if source_id == target_id: raise ValueError("Self-relations are not allowed.")
            if any(r["source"]==source_id and r["target"]==target_id and r["type"]==relation for r in d["relations"]): return False
            if len(d["relations"])>=self.MAX_RELATIONS: raise ValueError("Knowledge graph relation limit reached.")
            d["relations"].append({"id":uuid4().hex,"source":source_id,"type":relation,"target":target_id}); self._save(d); return True
    def list_entities(self, query="", limit=100):
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=500: raise ValueError("limit must be 1-500.")
        if not isinstance(query,str): raise ValueError("query must be text.")
        with self._lock:
            d=self._load(); q=query.casefold()
            return [dict(e) for e in d["entities"] if not q or q in e["name"].casefold() or q in e["description"].casefold()][:limit]
    def list_relations(self):
        with self._lock: return [dict(r) for r in self._load()["relations"]]
    def export_obsidian(self, directory):
        root=Path(directory).expanduser().resolve()
        with self._lock: data=self._load()
        root.mkdir(parents=True,exist_ok=True)
        byid={e["id"]:e for e in data["entities"]}
        exported=[]
        for e in data["entities"]:
            safe="".join(ch for ch in e["name"] if ch.isalnum() or ch in " -_").strip()[:100] or e["id"]
            safe=safe.replace("/","_").replace("\\","_")
            links=[]
            for r in data["relations"]:
                if r["source"]==e["id"] and r["target"] in byid: links.append(f"- {r['type']}: [[{byid[r['target']]['name']}]]")
                elif r["target"]==e["id"] and r["source"] in byid: links.append(f"- incoming {r['type']}: [[{byid[r['source']]['name']}]]")
            body=f"# {e['name']}\n\nType: {e['kind']}\n\n{e['description']}\n"
            if links: body += "\n## Relations\n"+ "\n".join(links)+"\n"
            target=root/(safe+".md"); target.write_text(body,encoding="utf-8"); exported.append(str(target))
        return exported
