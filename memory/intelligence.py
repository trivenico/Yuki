"""Conservative memory analysis: duplicate and contradiction candidates.

This module is advisory only. It never mutates, merges, deletes, or promotes
memories. Findings are untrusted suggestions requiring explicit review.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable, Sequence

from memory.collective_vault import CollectiveMemoryVault, MemoryRecord

_TOKEN = re.compile(r"[a-z0-9_]{2,}")
_NEGATION = frozenset({"not", "no", "never", "without", "cannot", "can't", "isn't", "aren't", "doesn't", "don't"})
_OPPOSITES = (
    (frozenset({"enable", "enabled", "activate", "active"}), frozenset({"disable", "disabled", "deactivate", "inactive"})),
    (frozenset({"allow", "allowed", "permit", "permitted"}), frozenset({"deny", "denied", "forbid", "forbidden"})),
    (frozenset({"true", "yes", "supported", "available"}), frozenset({"false", "no", "unsupported", "unavailable"})),
)


class MemoryIntelligenceError(ValueError):
    """Invalid memory analysis request or provider response."""


@dataclass(frozen=True)
class MemoryFinding:
    kind: str
    confidence: float
    left_memory_id: str
    right_memory_id: str
    explanation: str
    requires_human_review: bool = True


class MemoryIntelligenceEngine:
    """Find possible duplicate/conflicting records within explicitly allowed scopes."""

    def __init__(
        self,
        vault: CollectiveMemoryVault,
        *,
        embed=None,
        candidate_limit: int = 500,
        duplicate_threshold: float = 0.82,
        contradiction_threshold: float = 0.35,
    ):
        if not isinstance(vault, CollectiveMemoryVault):
            raise TypeError("vault must be a CollectiveMemoryVault.")
        if embed is not None and not callable(embed):
            raise TypeError("embed must be callable or None.")
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 5000:
            raise MemoryIntelligenceError("candidate_limit must be between 1 and 5000.")
        for name, value in (("duplicate_threshold", duplicate_threshold),
                            ("contradiction_threshold", contradiction_threshold)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise MemoryIntelligenceError(f"{name} must be between 0 and 1.")
        self.vault = vault
        self.embed = embed
        self.candidate_limit = candidate_limit
        self.duplicate_threshold = float(duplicate_threshold)
        self.contradiction_threshold = float(contradiction_threshold)

    @staticmethod
    def _tokens(content: str) -> set[str]:
        return set(_TOKEN.findall(content.casefold()))

    @staticmethod
    def _negated(tokens: set[str]) -> bool:
        return bool(tokens & _NEGATION)

    @staticmethod
    def _opposed(left: set[str], right: set[str]) -> bool:
        for positive, negative in _OPPOSITES:
            if (left & positive and right & negative) or (left & negative and right & positive):
                return True
        return False

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if isinstance(left, (str, bytes)) or isinstance(right, (str, bytes)):
            raise MemoryIntelligenceError("embeddings must be numeric sequences.")
        if not 1 <= len(left) <= 8192 or len(left) != len(right):
            raise MemoryIntelligenceError("embedding dimensions are invalid or inconsistent.")
        a, b = [], []
        for value in left:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise MemoryIntelligenceError("embedding values must be finite numbers.")
            a.append(float(value))
        for value in right:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise MemoryIntelligenceError("embedding values must be finite numbers.")
            b.append(float(value))
        na = math.sqrt(sum(x*x for x in a))
        nb = math.sqrt(sum(x*x for x in b))
        if not na or not nb:
            raise MemoryIntelligenceError("zero embeddings cannot be compared.")
        return max(-1.0, min(1.0, sum(x*y for x, y in zip(a, b)) / (na*nb)))

    def analyze(
        self, *, actor_id: str, scopes: Iterable[tuple[str, str]],
        limit: int = 100,
    ) -> tuple[MemoryFinding, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise MemoryIntelligenceError("limit must be between 1 and 1000.")
        records = self.vault.scoped_candidates(
            actor_id=actor_id, scopes=scopes, limit=self.candidate_limit
        )
        findings = []
        vectors = {}
        if self.embed is not None:
            try:
                for record in records:
                    vector = self.embed(record.content)
                    if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence) or not 1 <= len(vector) <= 8192:
                        raise MemoryIntelligenceError("embedding provider returned an invalid vector.")
                    vectors[record.memory_id] = tuple(vector)
            except MemoryIntelligenceError:
                raise
            except Exception as exc:
                raise MemoryIntelligenceError("embedding provider failed; analysis aborted.") from exc

        for index, left in enumerate(records):
            left_tokens = self._tokens(left.content)
            for right in records[index + 1:]:
                right_tokens = self._tokens(right.content)
                union = left_tokens | right_tokens
                overlap = len(left_tokens & right_tokens) / len(union) if union else 0.0
                similarity = overlap
                if self.embed is not None:
                    try:
                        similarity = (self._cosine(vectors[left.memory_id], vectors[right.memory_id]) + 1) / 2
                    except MemoryIntelligenceError:
                        raise
                    except Exception as exc:
                        raise MemoryIntelligenceError("embedding comparison failed; analysis aborted.") from exc

                if left.content.strip().casefold() == right.content.strip().casefold():
                    findings.append(MemoryFinding(
                        "exact_duplicate", 1.0, left.memory_id, right.memory_id,
                        "The normalized memory content is identical."
                    ))
                    continue

                if self._opposed(left_tokens, right_tokens) or (
                    self._negated(left_tokens) != self._negated(right_tokens)
                    and overlap >= self.contradiction_threshold
                ):
                    confidence = min(0.95, max(0.55, overlap + 0.25))
                    findings.append(MemoryFinding(
                        "possible_contradiction", round(confidence, 4),
                        left.memory_id, right.memory_id,
                        "The records share terminology but contain opposing terms or negation; inspect scope, date, and evidence."
                    ))
                elif similarity >= self.duplicate_threshold:
                    findings.append(MemoryFinding(
                        "possible_duplicate", round(similarity, 4),
                        left.memory_id, right.memory_id,
                        "The records have high lexical or embedding similarity; review provenance before consolidation."
                    ))

        findings.sort(key=lambda item: (item.confidence, item.kind), reverse=True)
        return tuple(findings[:limit])
