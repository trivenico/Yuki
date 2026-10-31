"""Searchable on-demand skill catalog for tool-using agents.

The catalog teaches agents how to select and use registered skills. It does not
load entrypoints, grant permissions, or bypass the shared invocation guard.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from core.guarded_skill_runtime import OneShotSkillApprovals, SkillInvocationResult, invoke_registered_skill

_TOKEN = re.compile(r"[a-zA-Z0-9_+-]{2,}")
_RISKS = {"low", "medium", "high", "critical"}


@dataclass(frozen=True)
class SkillMatch:
    card: "SkillCard"
    score: int
    matched_fields: tuple[str, ...]


@dataclass(frozen=True)
class SkillSelection:
    status: str
    selected: SkillMatch | None
    candidates: tuple[SkillMatch, ...]
    margin: int


@dataclass(frozen=True)
class SkillCard:
    name: str
    description: str
    risk: str
    tags: tuple[str, ...]
    usage: str
    agent_guidance: str
    examples: tuple[str, ...]
    requires_approval: bool


def _text(value: Any, default: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip()[:limit]


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _TOKEN.findall(value)}


class SkillCatalog:
    """Read-only catalog backed by a trusted registry."""

    def __init__(self, registry: Any):
        if not callable(getattr(registry, "get", None)) or not callable(getattr(registry, "names", None)):
            raise TypeError("registry must expose get() and names()")
        self._registry = registry

    def get(self, name: str) -> SkillCard | None:
        skill = self._registry.get(name)
        if skill is None:
            return None
        raw_tags = getattr(skill, "tags", ())
        if not isinstance(raw_tags, (list, tuple, set, frozenset)):
            raw_tags = ()
        tags = tuple(sorted({
            tag.strip().lower()[:40]
            for tag in raw_tags
            if isinstance(tag, str) and tag.strip()
        }))[:20]
        raw_examples = getattr(skill, "examples", ())
        if not isinstance(raw_examples, (list, tuple)):
            raw_examples = ()
        examples = tuple(
            item.strip()[:300]
            for item in raw_examples
            if isinstance(item, str) and item.strip()
        )[:5]
        risk = getattr(skill, "risk", "high")
        if not isinstance(risk, str) or risk.lower() not in _RISKS:
            risk = "high"
        return SkillCard(
            name=_text(getattr(skill, "name", None), name, 128),
            description=_text(getattr(skill, "description", None), "No description provided.", 1000),
            risk=risk.lower(),
            tags=tags,
            usage=_text(getattr(skill, "usage", None), "Pass a concise, task-specific text argument.", 500),
            agent_guidance=_text(
                getattr(skill, "agent_guidance", None),
                "Use only when the task matches this capability. Check required inputs first, validate the result, and request clarification before consequential actions when needed.",
                1500,
            ),
            examples=examples,
            requires_approval=bool(getattr(skill, "requires_approval", True)) or risk.lower() in {"high", "critical"},
        )

    def rank(self, query: str, *, limit: int = 8) -> tuple[SkillMatch, ...]:
        """Rank metadata matches deterministically; never imports or executes skills."""
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            return ()
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = 8
        limit = max(1, min(limit, 25))
        query_tokens = _tokens(query)
        if not query_tokens:
            return ()
        matches = []
        normalized_query = " ".join(query.lower().split())
        for name in self._registry.names():
            card = self.get(name)
            if card is None:
                continue
            fields = {
                "name": _tokens(card.name.replace("_", " ").replace("-", " ")),
                "tags": _tokens(" ".join(card.tags)),
                "description": _tokens(card.description),
                "usage": _tokens(card.usage),
                "examples": _tokens(" ".join(card.examples)),
            }
            weights = {"name": 5, "tags": 4, "description": 3, "usage": 2, "examples": 1}
            matched_fields = tuple(
                field for field, tokens in fields.items() if query_tokens & tokens
            )
            score = sum(
                weights[field] * len(query_tokens & tokens)
                for field, tokens in fields.items()
            )
            # A phrase match is useful evidence, but never sufficient by itself
            # to grant authorization or bypass the confidence threshold.
            searchable = (
                card.name.replace("_", " ").replace("-", " "),
                " ".join(card.tags), card.description, card.usage,
                " ".join(card.examples),
            )
            phrase_bonus = 0
            for field, value in zip(fields, searchable):
                normalized_value = " ".join(value.lower().split())
                if len(query_tokens) >= 2 and len(normalized_query) >= 4 and normalized_query in normalized_value:
                    phrase_bonus += weights[field]
            score += min(phrase_bonus, 8)
            if score:
                matches.append(SkillMatch(card, score, matched_fields))
        matches.sort(key=lambda match: (-match.score, match.card.name))
        return tuple(matches[:limit])

    def search(self, query: str, *, limit: int = 8) -> tuple[SkillCard, ...]:
        """Compatibility API returning ranked cards without confidence decisions."""
        return tuple(match.card for match in self.rank(query, limit=limit))

    def select(
        self, query: str, *, limit: int = 5, min_score: int = 4, min_margin: int = 2
    ) -> SkillSelection:
        """Select only a sufficiently strong, unambiguous lexical match.

        Ambiguous and weak matches fail closed. This method is discovery only;
        the caller must still obtain invocation authorization.
        """
        if isinstance(min_score, bool) or not isinstance(min_score, int):
            min_score = 4
        if isinstance(min_margin, bool) or not isinstance(min_margin, int):
            min_margin = 2
        min_score = max(1, min(min_score, 1000))
        min_margin = max(1, min(min_margin, 1000))
        matches = self.rank(query, limit=limit)
        if not matches or matches[0].score < min_score:
            return SkillSelection("not_found", None, matches, 0)
        margin = matches[0].score - matches[1].score if len(matches) > 1 else matches[0].score
        if len(matches) > 1 and margin < min_margin:
            return SkillSelection("ambiguous", None, matches, margin)
        return SkillSelection("selected", matches[0], matches, margin)

    def invoke(
        self,
        name: str,
        argument: str = "",
        *,
        approvals: OneShotSkillApprovals,
        audit=None,
    ) -> SkillInvocationResult:
        """Invoke only through Yuki's shared guarded skill execution boundary."""
        return invoke_registered_skill(
            self._registry, name, argument, approvals=approvals, audit=audit
        )
