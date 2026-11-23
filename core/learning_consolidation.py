"""Human-reviewed memory consolidation signals for Yuki.

The analyzer is deliberately conservative: it reports lexical overlap only.
It never edits, merges, archives, or deletes memory and cannot establish that
two statements are semantically equivalent or contradictory.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ConsolidationFinding:
    memory_ids: tuple[str, ...]
    memory_versions: tuple[int, ...]
    signal: str
    lexical_similarity: float


@dataclass(frozen=True)
class ConsolidationReport:
    memories_examined: int
    findings: tuple[ConsolidationFinding, ...]
    duplicate_review_count: int
    related_review_count: int


class MemoryConsolidator:
    """Generate deterministic review suggestions from a frozen owner snapshot."""

    MAX_MEMORIES = 200
    MAX_TEXT = 4000
    DUPLICATE_THRESHOLD = 0.85
    RELATED_THRESHOLD = 0.55

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[\w]{2,}", text.casefold())[:256])

    @classmethod
    def analyze(cls, entries: Sequence[Mapping[str, object]]) -> ConsolidationReport:
        if not isinstance(entries, (list, tuple)) or len(entries) > cls.MAX_MEMORIES:
            raise ValueError("entries must be a list or tuple of at most 200 memories.")

        validated: list[tuple[str, int, set[str]]] = []
        seen_ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise TypeError("each memory must be a mapping.")
            memory_id = entry.get("id")
            version = entry.get("version", 1)
            text = entry.get("text")
            status = entry.get("status", "active")
            if (
                not isinstance(memory_id, str) or not memory_id.strip()
                or len(memory_id) > 128 or memory_id in seen_ids
            ):
                raise ValueError("memory IDs must be unique non-empty text up to 128 characters.")
            if type(version) is not int or version < 1:
                raise ValueError("memory versions must be positive integers.")
            if not isinstance(text, str) or not text.strip() or len(text) > cls.MAX_TEXT:
                raise ValueError("memory text must be non-empty text up to 4000 characters.")
            if status not in {"active", "archived"}:
                raise ValueError("memory status must be active or archived.")
            seen_ids.add(memory_id)
            if status == "active":
                tokens = cls._tokens(text)
                if tokens:
                    validated.append((memory_id, version, tokens))

        findings: list[ConsolidationFinding] = []
        for index, (left_id, left_version, left_tokens) in enumerate(validated):
            for right_id, right_version, right_tokens in validated[index + 1:]:
                union = left_tokens | right_tokens
                similarity = len(left_tokens & right_tokens) / len(union)
                if similarity < cls.RELATED_THRESHOLD:
                    continue
                signal = (
                    "possible_duplicate_review"
                    if similarity >= cls.DUPLICATE_THRESHOLD
                    else "related_memory_review"
                )
                findings.append(ConsolidationFinding(
                    memory_ids=(left_id, right_id),
                    memory_versions=(left_version, right_version),
                    signal=signal,
                    lexical_similarity=round(similarity, 6),
                ))

        findings.sort(key=lambda item: (
            item.signal, -item.lexical_similarity, item.memory_ids
        ))
        duplicate_count = sum(
            item.signal == "possible_duplicate_review" for item in findings
        )
        return ConsolidationReport(
            memories_examined=len(validated),
            findings=tuple(findings),
            duplicate_review_count=duplicate_count,
            related_review_count=len(findings) - duplicate_count,
        )
