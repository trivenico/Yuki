"""Conservative contradiction candidate detection for Yuki memory.

This module does not decide which claim is true. It surfaces high-overlap
claims with explicit opposing polarity for owner review and preserves both
records and their provenance. Retrieved memory is untrusted data.
"""
from __future__ import annotations

from itertools import combinations
import re

from core.agent_memory import AgentMemoryStore


_NEGATION = frozenset({
    "not", "never", "no", "cannot", "can't", "dont", "doesnt", "isnt",
    "wasnt", "wont", "without", "neither", "nor",
})
_STOP = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "has", "have", "had", "to", "of", "in", "on",
    "at", "for", "with", "by", "and", "or", "that", "this", "it",
})
_OPPOSITES = (
    (frozenset({"enabled", "active", "on"}), frozenset({"disabled", "inactive", "off"})),
    (frozenset({"allowed", "permitted", "authorized"}), frozenset({"blocked", "denied", "forbidden", "unauthorized"})),
    (frozenset({"true", "yes"}), frozenset({"false", "no"})),
    (frozenset({"available", "online", "healthy"}), frozenset({"unavailable", "offline", "unhealthy"})),
    (frozenset({"present", "exists"}), frozenset({"absent", "missing"})),
)
_POLARITY_TERMS = frozenset().union(*(positive | negative for positive, negative in _OPPOSITES))
_MEASUREMENT = re.compile(
    r"(?<![a-z0-9])(-?\${ch}+(?:\.\${ch}+)?)\${ch}*(milliseconds?|ms|seconds?|secs?|s|minutes?|mins?|hours?|hrs?|days?|%|percent|gb|mb|tb|kb|hz|ghz|tokens?)(?![a-z])",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


class MemoryConflictDetector:
    """Find possible contradictions without mutating or adjudicating memory."""

    def __init__(self, memory_store: AgentMemoryStore):
        if not isinstance(memory_store, AgentMemoryStore):
            raise TypeError("memory_store_must_be_AgentMemoryStore")
        self.memory_store = memory_store

    @staticmethod
    def _features(content: str) -> tuple[set[str], set[str], bool]:
        tokens = set(_TOKEN.findall(content.casefold()))
        negated = bool(tokens & _NEGATION)
        canonical = set(tokens) - _STOP - _NEGATION - _POLARITY_TERMS
        canonical -= {match.group(1) for match in _MEASUREMENT.finditer(content)}
        return tokens, canonical, negated

    @staticmethod
    def _opposite_values(left: set[str], right: set[str]) -> bool:
        for positive, negative in _OPPOSITES:
            if (left & positive and right & negative) or (left & negative and right & positive):
                return True
        return False

    @classmethod
    def _candidate(cls, left: dict, right: dict, *, min_overlap: float) -> dict | None:
        left_tokens, left_core, left_negated = cls._features(left["content"])
        right_tokens, right_core, right_negated = cls._features(right["content"])
        if not left_core or not right_core:
            return None
        intersection = left_core & right_core
        union = left_core | right_core
        overlap = len(intersection) / len(union)
        explicit_opposites = cls._opposite_values(left_tokens, right_tokens)
        opposite_negation = left_negated != right_negated
        left_measurements = {
            (match.group(2).casefold(), float(match.group(1)))
            for match in _MEASUREMENT.finditer(left["content"])
        }
        right_measurements = {
            (match.group(2).casefold(), float(match.group(1)))
            for match in _MEASUREMENT.finditer(right["content"])
        }
        left_units = {unit for unit, _ in left_measurements}
        right_units = {unit for unit, _ in right_measurements}
        numeric_conflict = bool(
            left_units & right_units
            and {value for unit, value in left_measurements if unit in right_units}
            != {value for unit, value in right_measurements if unit in left_units}
        )
        if not explicit_opposites and not numeric_conflict and not (
            opposite_negation and overlap >= min_overlap
        ):
            return None
        if overlap < min_overlap:
            return None
        return {
            "kind": "possible_contradiction",
            "status": "needs_owner_review",
            "memory_ids": [left["memory_id"], right["memory_id"]],
            "agent_id": left["agent_id"],
            "scope": "shared" if left["scope"] == "shared" or right["scope"] == "shared" else "private",
            "claims": [
                {
                    "memory_id": left["memory_id"],
                    "content": left["content"],
                    "source": left["source"],
                    "confidence": left["confidence"],
                    "verified": left["verified"],
                    "created_at": left["created_at"],
                    "agent_id": left["agent_id"],
                    "scope": left["scope"],
                },
                {
                    "memory_id": right["memory_id"],
                    "content": right["content"],
                    "source": right["source"],
                    "confidence": right["confidence"],
                    "verified": right["verified"],
                    "created_at": right["created_at"],
                    "agent_id": right["agent_id"],
                    "scope": right["scope"],
                },
            ],
            "signals": {
                "token_overlap": round(overlap, 4),
                "opposing_negation": opposite_negation,
                "opposite_state_terms": explicit_opposites,
                "numeric_measurement_conflict": numeric_conflict,
                "left_measurements": sorted([list(item) for item in left_measurements]),
                "right_measurements": sorted([list(item) for item in right_measurements]),
            },
            "resolution": "none",
            "memory_mutated": False,
            "trust": "untrusted_reference_data",
        }

    def detect(
        self, *, actor_id: str, agent_id: str, limit: int = 100,
        include_shared: bool = True, min_overlap: float = 0.75,
    ) -> list[dict]:
        if type(limit) is not int or not 2 <= limit <= 100:
            raise ValueError("limit_must_be_2_to_100")
        if type(include_shared) is not bool:
            raise ValueError("include_shared_must_be_boolean")
        if (isinstance(min_overlap, bool) or not isinstance(min_overlap, (int, float))
                or not 0.5 <= min_overlap <= 1.0):
            raise ValueError("min_overlap_must_be_between_0_5_and_1")
        memories = self.memory_store.list_memories(
            actor_id=actor_id, agent_id=agent_id, limit=limit,
            include_shared=include_shared,
        )
        candidates = []
        for left, right in combinations(memories, 2):
            candidate = self._candidate(left, right, min_overlap=float(min_overlap))
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(
            key=lambda item: (
                item["signals"]["token_overlap"],
                max(claim["created_at"] for claim in item["claims"]),
                tuple(item["memory_ids"]),
            ),
            reverse=True,
        )
        return candidates
