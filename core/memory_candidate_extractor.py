"""Conservative, deterministic extraction of owner-memory review candidates.

Only explicit first-person preference/context statements are proposed. This
module never writes memory, never calls a model, and never treats assistant
messages as owner evidence. Every result requires owner review before storage.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence


_MAX_MESSAGES = 20
_MAX_TEXT = 2_000
_MAX_CANDIDATES = 10
_SENSITIVE = re.compile(
    r"\b(password|passphrase|api[ _-]?key|access[ _-]?token|refresh[ _-]?token|"
    r"secret|private[ _-]?key|recovery[ _-]?code|credit[ _-]?card|"
    r"social[ _-]?security|bank[ _-]?account)\b",
    re.IGNORECASE,
)
_PATTERNS = (
    (re.compile(r"\bI\s+(?:strongly\s+)?prefer\s+(.+)", re.IGNORECASE), "preference"),
    (re.compile(r"\bI\s+(?:always|usually|typically)\s+(.+)", re.IGNORECASE), "preference"),
    (re.compile(r"\bI\s+(?:work|develop|build|maintain)\s+(?:with|on|using)\s+(.+)", re.IGNORECASE), "context"),
    (re.compile(r"\bI\s+use\s+(.+)", re.IGNORECASE), "context"),
    (re.compile(r"\bI\s+am\s+working\s+on\s+(.+)", re.IGNORECASE), "context"),
    (re.compile(r"\bI['’]m\s+working\s+on\s+(.+)", re.IGNORECASE), "context"),
    (re.compile(r"\bremember\s+that\s+(.+)", re.IGNORECASE), "explicit"),
    (re.compile(r"\bplease\s+remember\s+(.+)", re.IGNORECASE), "explicit"),
    (re.compile(r"\bmy\s+(?:preferred|default)\s+(.+)", re.IGNORECASE), "preference"),
)
_TRIM = " \t\r\n.,;:!?"
_MIN_CLAIM = 8


def _source_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError(f"invalid_{field}")
    return value.strip()


def extract_memory_candidates(
    messages: Sequence[Mapping[str, object]],
    *,
    conversation_id: str,
    max_candidates: int = _MAX_CANDIDATES,
) -> list[dict]:
    """Return review proposals from explicit user statements, without saving.

    Input messages must contain role, content, and message_id. Only role=user is
    considered. The source message ID and conversation ID are retained so a
    reviewer can inspect provenance. Sensitive credential/payment claims are
    excluded rather than copied into the memory review queue.
    """
    conversation_id = _source_id(conversation_id, "conversation_id")
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise TypeError("messages_must_be_a_sequence")
    if len(messages) > _MAX_MESSAGES:
        raise ValueError("too_many_messages")
    if type(max_candidates) is not int or not 1 <= max_candidates <= _MAX_CANDIDATES:
        raise ValueError("max_candidates_must_be_1_to_10")

    candidates: list[dict] = []
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("message_must_be_a_mapping")
        if message.get("role") != "user":
            continue
        message_id = _source_id(message.get("message_id"), "message_id")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT:
            continue
        if _SENSITIVE.search(text):
            continue

        for pattern, category in _PATTERNS:
            match = pattern.search(text.strip())
            if match is None:
                continue
            claim = match.group(1).strip(_TRIM)
            if len(claim) < _MIN_CLAIM or _SENSITIVE.search(claim):
                continue
            normalized = " ".join(claim.casefold().split())
            fingerprint = hashlib.sha256(
                (category + "\0" + normalized).encode("utf-8")
            ).hexdigest()
            if fingerprint in seen:
                break
            seen.add(fingerprint)
            candidates.append({
                "candidate_id": "memcand-" + fingerprint[:24],
                "content": claim[:_MAX_TEXT],
                "category": category,
                "status": "pending_owner_review",
                "source": {
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "role": "user",
                },
                "fingerprint": fingerprint,
                "memory_written": False,
                "requires_owner_approval": True,
                "trust": "untrusted_owner_evidence",
            })
            break
        if len(candidates) >= max_candidates:
            break
    return candidates
