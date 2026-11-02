"""Conservative, deterministic memory candidate extraction.

This module never writes memories or proposals. It extracts transparent,
rule-based suggestions for a later human review step. It is intentionally not
an LLM and does not claim semantic understanding.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from core.memory import Memory, MemoryStore

_MAX_INPUT = 12_000
_MAX_CANDIDATES = 12

_RULES = (
    (re.compile(r"\b(?:please\s+)?remember\s+that\s+(.+)", re.I), "fact", 0.96),
    (re.compile(r"\b(?:i|we)\s+(?:always\s+)?prefer\s+(.+)", re.I), "preference", 0.91),
    (re.compile(r"\b(?:our\s+project|this\s+project)\s+(?:uses|is\s+built\s+with)\s+(.+)", re.I), "project", 0.88),
    (re.compile(r"\b(?:we|i)\s+(?:have\s+)?decided\s+to\s+(.+)", re.I), "decision", 0.88),
    (re.compile(r"\b(?:our|the)\s+decision\s+is\s+(.+)", re.I), "decision", 0.88),
    (re.compile(r"\b(?:the\s+)?workflow\s+is\s+to\s+(.+)", re.I), "procedure", 0.82),
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class MemoryCandidate:
    category: str
    content: str
    evidence: str
    confidence: float
    duplicate_of: str | None = None
    possible_conflict_with: tuple[str, ...] = ()


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _similarity(left: str, right: str) -> float:
    a, b = set(_normalize(left).split()), set(_normalize(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _conflicts(candidate: MemoryCandidate, existing: Iterable[Memory]) -> tuple[str, ...]:
    # Flag potentially contradictory statements within the same category.
    # This is deliberately conservative: it signals review, never overwrites.
    tokens = set(_normalize(candidate.content).split())
    negations = {"not", "never", "avoid", "without", "no"}
    found = []
    for item in existing:
        if item.category != candidate.category:
            continue
        other = set(_normalize(item.content).split())
        overlap = len(tokens & other) / max(1, len(tokens | other))
        polarity_differs = bool(tokens & negations) != bool(other & negations)
        if overlap >= 0.45 and polarity_differs:
            found.append(item.memory_id)
    return tuple(found)


class MemoryIntelligence:
    """Extract and score candidate memories without persisting any content."""

    def __init__(self, memory_store: MemoryStore | None = None):
        self.memory_store = memory_store

    def extract(self, text: str, *, actor_id: str | None = None,
                scope_id: str | None = None) -> list[MemoryCandidate]:
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_INPUT:
            raise ValueError("text must be non-empty and at most 12000 characters")
        if (actor_id is None) != (scope_id is None):
            raise ValueError("actor_id and scope_id must be supplied together")
        existing: list[Memory] = []
        if self.memory_store is not None and actor_id is not None and scope_id is not None:
            existing = self.memory_store.list(actor_id=actor_id, scope_id=scope_id, limit=200)

        candidates: list[MemoryCandidate] = []
        seen: set[tuple[str, str]] = set()
        for sentence in _SENTENCE_SPLIT.split(text.strip()):
            evidence = sentence.strip()
            if not evidence or len(evidence) > 2_000:
                continue
            for pattern, category, confidence in _RULES:
                match = pattern.search(evidence)
                if not match:
                    continue
                content = match.group(1).strip(" \t\r\n.!?;:")
                if len(content) < 4 or len(content) > 500:
                    continue
                key = (category, _normalize(content))
                if key in seen:
                    break
                seen.add(key)
                candidate = MemoryCandidate(category, content, evidence, confidence)
                duplicate = next((item for item in existing
                    if item.category == category and _similarity(item.content, content) >= 0.8), None)
                conflicts = _conflicts(candidate, existing)
                candidates.append(MemoryCandidate(
                    category, content, evidence, confidence,
                    duplicate.memory_id if duplicate else None, conflicts,
                ))
                break
            if len(candidates) >= _MAX_CANDIDATES:
                break
        return candidates
