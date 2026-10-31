"""Bounded NDJSON event protocol for agent-driven Yuki CLI runs.

Events are transport records, not authorization. This module never invokes tools.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

MAX_EVENT_BYTES = 32_000
MAX_TEXT_CHARS = 24_000
EVENT_TYPES = frozenset({"start", "content", "done", "error"})


@dataclass(frozen=True)
class AgentCLIEvent:
    event_type: str
    session_id: str
    request_id: str
    data: dict[str, Any]

    def encode(self) -> str:
        if self.event_type not in EVENT_TYPES:
            raise ValueError("Unsupported agent CLI event type.")
        if not isinstance(self.session_id, str) or not self.session_id or len(self.session_id) > 64:
            raise ValueError("Invalid session ID.")
        if not isinstance(self.request_id, str) or not self.request_id or len(self.request_id) > 64:
            raise ValueError("Invalid request ID.")
        if not isinstance(self.data, dict):
            raise ValueError("Event data must be an object.")
        try:
            encoded = json.dumps({
                "type": self.event_type, "session_id": self.session_id,
                "request_id": self.request_id, "data": self.data,
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Event data must contain finite JSON-compatible values.") from exc
        if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
            raise ValueError("Agent CLI event exceeds the 32 KB limit.")
        return encoded


def new_session_id() -> str:
    return uuid4().hex


def new_request_id() -> str:
    return uuid4().hex


def event_line(event_type: str, session_id: str, request_id: str, **data: Any) -> str:
    return AgentCLIEvent(event_type, session_id, request_id, data).encode()


def content_events(text: str, *, session_id: str, request_id: str, chunk_chars: int = 3000) -> Iterable[str]:
    """Yield bounded content records without splitting Unicode code points."""
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise ValueError("Provider output must be text of at most 24000 characters.")
    if isinstance(chunk_chars, bool) or not isinstance(chunk_chars, int) or not 1 <= chunk_chars <= 6000:
        raise ValueError("chunk_chars must be between 1 and 6000.")
    for offset in range(0, len(text), chunk_chars):
        yield event_line("content", session_id, request_id, text=text[offset:offset + chunk_chars])