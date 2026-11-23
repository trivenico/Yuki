"""Provider-independent intent routing for Yuki's native intelligence layer.

This module performs bounded, deterministic capability matching. It never
executes a skill, grants permission, or treats a match as authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

_TOKEN = re.compile(r"[\w+-]{2,}", re.UNICODE)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could",
    "do", "for", "from", "how", "i", "in", "is", "it", "me", "my",
    "of", "on", "or", "please", "show", "the", "this", "to", "what",
    "with", "would", "you", "your", "tell", "give",
}
_SYNONYMS = {
    "find": {"search", "locate", "discover"},
    "look": {"search", "find", "locate"},
    "lookup": {"search", "research", "find"},
    "locate": {"find", "search"},
    "tell": {"show", "display", "current"},
    "give": {"show", "display", "current"},
    "search": {"find", "locate", "lookup"},
    "investigate": {"research", "analyze", "examine"},
    "examine": {"analyze", "inspect", "research"},
    "inspect": {"examine", "analyze", "review"},
    "analyze": {"examine", "inspect", "research"},
    "study": {"learn", "research", "understand"},
    "learn": {"study", "teach", "explain"},
    "explain": {"describe", "teach", "understand"},
    "document": {"file", "notes", "workspace"},
    "files": {"file", "documents", "workspace"},
    "documents": {"document", "files", "workspace"},
    "bug": {"debug", "error", "issue"},
    "debug": {"bug", "error", "troubleshoot"},
    "fix": {"repair", "debug", "troubleshoot"},
    "email": {"mail", "message", "gmail"},
    "mail": {"email", "message"},
}


@dataclass(frozen=True)
class IntentCandidate:
    name: str
    score: int
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class IntentResolution:
    status: str
    selected: IntentCandidate | None
    alternatives: tuple[IntentCandidate, ...]
    reason: str


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    result = set()
    for token in _TOKEN.findall(normalized):
        if token in _STOP_WORDS:
            continue
        result.add(token)
        if len(result) >= 256:
            break
    return result


def _expanded(tokens: set[str]) -> set[str]:
    result = set(tokens)
    for token in tokens:
        result.update(_SYNONYMS.get(token, ()))
    return result


class NativeIntentRouter:
    """Rank registered capabilities and abstain when intent is uncertain."""

    def __init__(self, *, minimum_score: int = 3, ambiguity_margin: int = 2):
        if type(minimum_score) is not int or not 1 <= minimum_score <= 100:
            raise ValueError("minimum_score must be an integer from 1 to 100.")
        if type(ambiguity_margin) is not int or not 0 <= ambiguity_margin <= 100:
            raise ValueError("ambiguity_margin must be an integer from 0 to 100.")
        self.minimum_score = minimum_score
        self.ambiguity_margin = ambiguity_margin

    def resolve(self, query: str, cards: Iterable[object]) -> IntentResolution:
        if not isinstance(query, str) or not query.strip() or len(query) > 20_000:
            return IntentResolution("no_match", None, (), "empty_or_invalid_query")
        query_tokens = _tokens(query)
        if not query_tokens:
            return IntentResolution("no_match", None, (), "no_meaningful_terms")
        expanded_query = _expanded(query_tokens)
        candidates: list[IntentCandidate] = []

        for card in cards:
            name = getattr(card, "name", None)
            if not isinstance(name, str) or not name:
                continue
            fields = {
                "name": _tokens(name.replace("_", " ").replace("-", " ")),
                "tags": _tokens(" ".join(getattr(card, "tags", ()) or ())),
                "description": _tokens(str(getattr(card, "description", ""))),
                "usage": _tokens(str(getattr(card, "usage", ""))),
                "examples": _tokens(" ".join(getattr(card, "examples", ()) or ())),
            }
            score = 8 if name.casefold().replace("_", " ") in unicodedata.normalize("NFKC", query).casefold() else 0
            score += 5 * len(expanded_query & fields["name"])
            score += 4 * len(expanded_query & fields["tags"])
            score += 2 * len(expanded_query & fields["description"])
            score += len(expanded_query & fields["usage"])
            score += len(expanded_query & fields["examples"])
            matched = tuple(sorted(expanded_query & (
                fields["name"] | fields["tags"] | fields["description"]
                | fields["usage"] | fields["examples"]
            )))
            if score:
                candidates.append(IntentCandidate(name, score, matched))

        candidates.sort(key=lambda item: (-item.score, item.name))
        ranked = tuple(candidates[:8])
        if not ranked or ranked[0].score < self.minimum_score:
            return IntentResolution("no_match", None, ranked, "insufficient_evidence")
        coverage = min(1.0, len(ranked[0].matched_terms) / len(query_tokens))
        if coverage < 0.6:
            return IntentResolution("no_match", None, ranked, "insufficient_query_coverage")
        if len(ranked) > 1 and ranked[0].score - ranked[1].score <= self.ambiguity_margin:
            return IntentResolution("ambiguous", None, ranked, "competing_capabilities")
        return IntentResolution("matched", ranked[0], ranked[1:], "strongest_capability_match")
