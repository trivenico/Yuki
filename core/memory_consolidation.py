"""Owner-scoped, read-only memory consolidation suggestions.

This module only identifies likely duplicate memories. It never merges, edits,
archives, deletes, or trains from memory. Every suggestion requires a separate
owner review and a version-fenced lifecycle action.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence


@dataclass(frozen=True)
class MemoryConsolidationProposal:
    suggested_keep_id: str
    suggested_archive_id: str
    keep_version: int
    archive_version: int
    similarity: float
    reason: str


class MemoryConsolidator:
    """Generate bounded duplicate suggestions from one owner's memory snapshot."""

    MAX_MEMORIES = 200
    MAX_PROPOSALS = 100

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return set(re.findall(r"[\w]{2,}", value.casefold()))

    @classmethod
    def _similarity(cls, left: str, right: str) -> float:
        left_tokens = cls._tokens(left)
        right_tokens = cls._tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    @staticmethod
    def _validate_entry(entry: object) -> tuple[str, int, str, float, int, str]:
        if not isinstance(entry, dict):
            raise ValueError("memory snapshot contains an invalid entry.")
        memory_id = entry.get("id")
        version = entry.get("version", 1)
        text = entry.get("text")
        confidence = entry.get("confidence", 0.5)
        use_count = entry.get("use_count", 0)
        created_at = entry.get("created_at", "")
        status = entry.get("status", "active")
        if not isinstance(memory_id, str) or not memory_id.strip() or len(memory_id) > 128:
            raise ValueError("memory snapshot contains an invalid ID.")
        if type(version) is not int or version < 1:
            raise ValueError("memory snapshot contains an invalid version.")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("memory snapshot contains invalid text.")
        if (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError("memory snapshot contains invalid confidence.")
        if type(use_count) is not int or use_count < 0:
            raise ValueError("memory snapshot contains an invalid use count.")
        if not isinstance(created_at, str) or len(created_at) > 64:
            raise ValueError("memory snapshot contains an invalid timestamp.")
        if status not in {"active", "archived"}:
            raise ValueError("memory snapshot contains an invalid lifecycle status.")
        return memory_id, version, text, float(confidence), use_count, created_at

    @classmethod
    def propose(
        cls,
        entries: Sequence[dict],
        *,
        similarity_threshold: float = 0.9,
        limit: int = 20,
    ) -> list[MemoryConsolidationProposal]:
        """Return duplicate candidates; input must be a single-owner snapshot.

        The suggested keeper is a deterministic hint based on confidence,
        retrieval usage, and age metadata—not a truth judgment. Callers must
        obtain explicit owner approval before any lifecycle update.
        """
        if not isinstance(entries, (list, tuple)) or len(entries) > cls.MAX_MEMORIES:
            raise ValueError("entries must contain at most 200 memory records.")
        if (
            isinstance(similarity_threshold, bool)
            or not isinstance(similarity_threshold, (int, float))
            or not 0.8 <= similarity_threshold <= 1.0
        ):
            raise ValueError("similarity_threshold must be between 0.8 and 1.0.")
        if type(limit) is not int or not 1 <= limit <= cls.MAX_PROPOSALS:
            raise ValueError("limit must be between 1 and 100.")

        parsed = [cls._validate_entry(entry) for entry in entries]
        ids = [item[0] for item in parsed]
        if len(ids) != len(set(ids)):
            raise ValueError("memory snapshot contains duplicate IDs.")

        active = [
            item for item, entry in zip(parsed, entries)
            if entry.get("status", "active") == "active"
        ]
        proposals: list[MemoryConsolidationProposal] = []
        for index, left in enumerate(active):
            for right in active[index + 1:]:
                score = cls._similarity(left[2], right[2])
                if score < similarity_threshold:
                    continue
                # Deterministic suggestion only; owner review remains mandatory.
                left_rank = (left[3], left[4], left[5], left[0])
                right_rank = (right[3], right[4], right[5], right[0])
                keep, archive = (left, right) if left_rank >= right_rank else (right, left)
                proposals.append(MemoryConsolidationProposal(
                    suggested_keep_id=keep[0],
                    suggested_archive_id=archive[0],
                    keep_version=keep[1],
                    archive_version=archive[1],
                    similarity=round(score, 6),
                    reason="high lexical overlap; inspect both memories before deciding whether to consolidate",
                ))
        proposals.sort(
            key=lambda item: (-item.similarity, item.suggested_keep_id, item.suggested_archive_id)
        )
        return proposals[:limit]
