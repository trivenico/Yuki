"""Synchronize explicitly verified evidence claims into Yuki's local graph.

This is a one-way, opt-in projection. Unreviewed, contested, rejected or stale
claims never become graph entities or relations.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4


class VerifiedKnowledgeGraphSync:
    MAX_ENTITIES_PER_CLAIM = 20
    MAX_RELATIONS_PER_CLAIM = 40

    def __init__(self, evidence_ledger, knowledge_graph,
                 database_path="YukiData/knowledge-graph-sync.sqlite3"):
        if not callable(getattr(evidence_ledger, "get_claim", None)):
            raise TypeError("evidence_ledger must provide get_claim().")
        if not callable(getattr(knowledge_graph, "add_entity", None)) or not callable(
            getattr(knowledge_graph, "add_relation", None)
        ):
            raise TypeError("knowledge_graph must provide add_entity() and add_relation().")
        self.evidence_ledger = evidence_ledger
        self.knowledge_graph = knowledge_graph
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(str(self.database_path), timeout=10.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def _session(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize(self):
        with self._lock, self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS verified_graph_sync (
                    claim_id TEXT PRIMARY KEY,
                    graph_entities_json TEXT NOT NULL,
                    graph_relations_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    plan_hash TEXT NOT NULL DEFAULT '',
                    synced_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
            """)
            columns = {row["name"] for row in db.execute(
                "PRAGMA table_info(verified_graph_sync)"
            ).fetchall()}
            if "plan_hash" not in columns:
                db.execute(
                    "ALTER TABLE verified_graph_sync ADD COLUMN plan_hash TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _validate_entities(entities):
        if not isinstance(entities, list) or not 1 <= len(entities) <= 20:
            raise ValueError("Provide 1-20 graph entities.")
        prepared = []
        seen = set()
        for item in entities:
            if not isinstance(item, dict):
                raise ValueError("Each entity must be an object.")
            name, kind = item.get("name"), item.get("kind", "concept")
            description = item.get("description", "")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
                raise ValueError("Entity names must contain 1-200 characters.")
            if kind not in {"person", "preference", "fact", "event", "place",
                            "project", "organization", "concept", "skill", "media"}:
                raise ValueError("Unsupported entity type.")
            if not isinstance(description, str) or len(description) > 2000:
                raise ValueError("Entity descriptions must be at most 2000 characters.")
            key = (name.strip().casefold(), kind)
            if key in seen:
                raise ValueError("Duplicate entity name and type in one claim.")
            seen.add(key)
            prepared.append({"name": name.strip(), "kind": kind,
                             "description": description.strip()})
        return prepared

    @staticmethod
    def _validate_relations(relations, entities):
        if not isinstance(relations, list) or len(relations) > 40:
            raise ValueError("Provide at most 40 graph relations.")
        names = {item["name"].casefold() for item in entities}
        prepared = []
        for item in relations:
            if not isinstance(item, dict):
                raise ValueError("Each relation must be an object.")
            source, target, relation = item.get("source"), item.get("target"), item.get("type")
            if not isinstance(source, str) or not isinstance(target, str):
                raise ValueError("Relation endpoints must be entity names.")
            if source.casefold() not in names or target.casefold() not in names:
                raise ValueError("Relation endpoints must be included in the claim entity set.")
            if not isinstance(relation, str) or not relation.strip() or len(relation) > 80:
                raise ValueError("Relation type must contain 1-80 characters.")
            if not relation.replace("_", "").replace("-", "").isalnum():
                raise ValueError("Relation type must be a short identifier.")
            if source.casefold() == target.casefold():
                raise ValueError("Self-relations are not allowed.")
            prepared.append({"source": source.strip(), "target": target.strip(),
                             "type": relation.strip()})
        return prepared

    def sync_claim(self, claim_id, *, entities, relations=()):
        """Project one verified claim. The caller supplies reviewed graph structure."""
        claim = self.evidence_ledger.get_claim(claim_id)
        if claim["status"] != "verified":
            raise ValueError("Only explicitly verified claims can be synchronized.")
        evidence = claim["evidence"]
        if not evidence or any(not item["current"] for item in evidence):
            raise ValueError("Claim evidence is missing or stale.")
        supporting = [item for item in evidence if item["stance"] == "supports"]
        source_ids = {item["source_id"] for item in supporting}
        if len(source_ids) < 2:
            raise ValueError("Verified claim no longer has two current supporting sources.")
        if not any(review["decision"] == "verify" for review in claim["reviews"]):
            raise ValueError("Claim has no explicit verification review.")

        prepared_entities = self._validate_entities(entities)
        prepared_relations = self._validate_relations(list(relations), prepared_entities)
        evidence_ids = sorted(item["evidence_id"] for item in supporting)
        plan = {
            "claim_text": claim["claim_text"],
            "entities": prepared_entities,
            "relations": prepared_relations,
            "evidence_ids": evidence_ids,
        }
        plan_hash = hashlib.sha256(
            json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        with self._lock, self._session() as db:
            existing = db.execute(
                "SELECT * FROM verified_graph_sync WHERE claim_id=?", (claim_id,)
            ).fetchone()
            if existing:
                if existing["plan_hash"] != plan_hash:
                    raise ValueError("Claim, evidence or graph projection changed after synchronization; review and reconcile before retry.")
                return {
                    "claim_id": claim_id, "already_synced": True,
                    "entity_ids": json.loads(existing["graph_entities_json"]),
                    "relation_ids": json.loads(existing["graph_relations_json"]),
                }

            # Graph writes are idempotent. If the process stops before the audit
            # record commits, retrying reuses entities and duplicate relations.
            graph_entities = {}
            for item in prepared_entities:
                entity = self.knowledge_graph.add_entity(
                    item["name"], kind=item["kind"], description=item["description"]
                )
                graph_entities[item["name"].casefold()] = entity["id"]

            relation_ids = []
            for item in prepared_relations:
                source_id = graph_entities[item["source"].casefold()]
                target_id = graph_entities[item["target"].casefold()]
                self.knowledge_graph.add_relation(source_id, item["type"], target_id)
                relation_ids.append({
                    "source": source_id, "type": item["type"], "target": target_id
                })

            entity_ids = sorted(set(graph_entities.values()))
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO verified_graph_sync
                       (claim_id, graph_entities_json, graph_relations_json, evidence_ids_json, plan_hash)
                       VALUES (?, ?, ?, ?, ?)""",
                    (claim_id, json.dumps(entity_ids), json.dumps(relation_ids),
                     json.dumps(evidence_ids), plan_hash),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {
            "claim_id": claim_id, "already_synced": False,
            "entity_ids": entity_ids, "relation_ids": relation_ids,
        }

    def get_sync(self, claim_id):
        with self._lock, self._session() as db:
            row = db.execute(
                "SELECT * FROM verified_graph_sync WHERE claim_id=?", (claim_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "claim_id": row["claim_id"],
            "entity_ids": json.loads(row["graph_entities_json"]),
            "relation_ids": json.loads(row["graph_relations_json"]),
            "evidence_ids": json.loads(row["evidence_ids_json"]),
            "plan_hash": row["plan_hash"],
            "synced_at": row["synced_at"],
        }
