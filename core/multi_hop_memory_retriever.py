"""Bounded multi-hop retrieval over permission-filtered Yuki memory.

Every hop calls UnifiedKnowledgeAPI with the original authenticated scope.
Expansion terms are deterministic tokens extracted from evidence; evidence
never changes authorization flags and is never executed as instructions.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Any

from core.unified_knowledge_api import UnifiedKnowledgeAPI


class MultiHopMemoryRetriever:
    """Explore a small evidence neighborhood without changing access scope."""

    MAX_HOPS = 3
    MAX_QUERIES = 6
    MAX_RECORDS = 30
    MAX_QUERY_CHARS = 1000
    _STOP_WORDS = frozenset({
        "about", "after", "also", "because", "before", "being", "could",
        "does", "from", "have", "into", "more", "most", "other", "over",
        "should", "that", "their", "there", "these", "they", "this", "those",
        "through", "under", "using", "were", "what", "when", "where", "which",
        "while", "with", "would", "your", "memory", "record", "notes",
    })

    def __init__(
        self,
        knowledge_api: UnifiedKnowledgeAPI,
        *,
        max_hops: int = 3,
        max_queries: int = 6,
        max_records: int = 30,
        per_query_limit: int = 10,
    ):
        if not isinstance(knowledge_api, UnifiedKnowledgeAPI):
            raise TypeError("knowledge_api_must_be_UnifiedKnowledgeAPI")
        for value, name, maximum in (
            (max_hops, "max_hops", self.MAX_HOPS),
            (max_queries, "max_queries", self.MAX_QUERIES),
            (max_records, "max_records", self.MAX_RECORDS),
            (per_query_limit, "per_query_limit", 10),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name}_must_be_1_to_{maximum}")
        self.knowledge_api = knowledge_api
        self.max_hops = max_hops
        self.max_queries = max_queries
        self.max_records = max_records
        self.per_query_limit = per_query_limit

    @classmethod
    def _expand_from_evidence(cls, content: str) -> str | None:
        """Extract a small keyword query, never copy evidence instructions."""
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{2,}", content.casefold())
        selected = []
        seen = set()
        for token in tokens:
            if token in cls._STOP_WORDS or token in seen:
                continue
            seen.add(token)
            selected.append(token)
            if len(selected) == 3:
                break
        return " ".join(selected)[:250] or None

    def retrieve(
        self,
        *,
        actor_id: str,
        agent_id: str,
        query: str,
        include_shared: bool = True,
        verified_only: bool = True,
    ) -> dict[str, Any]:
        """Return unique evidence, query lineage, and proposed retrieval edges.

        Errors from any hop propagate. A partial result is never returned as a
        successful complete search after an authorization or backend failure.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > self.MAX_QUERY_CHARS:
            raise ValueError("invalid_query")
        if "\x00" in query:
            raise ValueError("invalid_query")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id_required")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id_required")
        if type(include_shared) is not bool or type(verified_only) is not bool:
            raise ValueError("retrieval_flags_must_be_boolean")

        frontier = deque([(query.strip(), None, 0)])
        scheduled = {query.strip().casefold()}
        records: dict[str, dict] = {}
        queries = []
        edges = []
        hop_count = 0
        truncated = False

        while frontier and len(queries) < self.max_queries and len(records) < self.max_records:
            current_query, source_memory_id, hop = frontier.popleft()
            if hop > self.max_hops:
                continue
            hop_count = max(hop_count, hop)
            remaining = self.max_records - len(records)
            packet = self.knowledge_api.retrieve(
                actor_id=actor_id.strip(),
                agent_id=agent_id.strip(),
                query=current_query,
                limit=min(self.per_query_limit, remaining),
                include_shared=include_shared,
                verified_only=verified_only,
            )
            queries.append({
                "query": current_query,
                "hop": hop,
                "source_memory_id": source_memory_id,
            })
            evidence_items = packet.get("evidence", [])
            for item in evidence_items:
                memory_id = item.get("memory_id")
                if not isinstance(memory_id, str) or not memory_id:
                    continue
                if memory_id not in records and len(records) < self.max_records:
                    records[memory_id] = item
                if source_memory_id and memory_id != source_memory_id:
                    edge = {
                        "source_memory_id": source_memory_id,
                        "target_memory_id": memory_id,
                        "relationship": "retrieved_via_evidence_query",
                        "proposed": True,
                        "confidence": None,
                        "is_permission": False,
                    }
                    if edge not in edges:
                        edges.append(edge)

            if len(records) >= self.max_records:
                truncated = True
            if hop >= self.max_hops:
                continue

            for item in evidence_items:
                memory_id = item.get("memory_id")
                content = item.get("content")
                if not isinstance(memory_id, str) or not isinstance(content, str):
                    continue
                expanded = self._expand_from_evidence(content)
                if not expanded:
                    continue
                key = expanded.casefold()
                if key in scheduled:
                    continue
                if len(scheduled) >= self.max_queries:
                    truncated = True
                    continue
                scheduled.add(key)
                frontier.append((expanded, memory_id, hop + 1))

        if frontier:
            truncated = True
        return {
            "actor_id": actor_id.strip(),
            "agent_id": agent_id.strip(),
            "query": query.strip(),
            "evidence": list(records.values()),
            "count": len(records),
            "queries": queries,
            "query_count": len(queries),
            "hops_completed": hop_count,
            "edges": edges,
            "complete": not truncated,
            "limits": {
                "max_hops": self.max_hops,
                "max_queries": self.max_queries,
                "max_records": self.max_records,
                "per_query_limit": self.per_query_limit,
            },
            "policy": {
                "scope_is_fixed_for_every_hop": True,
                "authorization_checked_on_every_hop": True,
                "retrieved_content_is_untrusted_data": True,
                "expansion_is_deterministic_keyword_extraction": True,
                "edges_are_proposals_not_truth": True,
                "edges_do_not_grant_permissions": True,
                "retrieval_is_read_only": True,
            },
        }
