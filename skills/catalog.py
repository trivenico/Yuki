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

    def search(self, query: str, *, limit: int = 8) -> tuple[SkillCard, ...]:
        """Return deterministic lexical matches; search never grants authorization."""
        if not isinstance(query, str) or not query.strip():
            return ()
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = 8
        limit = max(1, min(limit, 25))
        query_tokens = _tokens(query)
        if not query_tokens:
            return ()
        matches = []
        for name in self._registry.names():
            card = self.get(name)
            if card is None:
                continue
            name_tokens = _tokens(card.name.replace("_", " ").replace("-", " "))
            tag_tokens = _tokens(" ".join(card.tags))
            description_tokens = _tokens(card.description)
            usage_tokens = _tokens(card.usage)
            score = (
                5 * len(query_tokens & name_tokens)
                + 4 * len(query_tokens & tag_tokens)
                + 3 * len(query_tokens & description_tokens)
                + len(query_tokens & usage_tokens)
            )
            if score:
                matches.append((score, card.name, card))
        matches.sort(key=lambda row: (-row[0], row[1]))
        return tuple(row[2] for row in matches[:limit])

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
