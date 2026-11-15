"""Shared bounded JSON request-body reader for ASGI APIs."""
from __future__ import annotations

import json


class RequestBodyTooLarge(ValueError):
    """Raised when an inbound ASGI request exceeds its endpoint body limit."""


async def read_json_object(receive, *, max_bytes: int) -> dict:
    """Read one JSON object without buffering more than max_bytes + 1 bytes."""
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    chunks = bytearray()
    while True:
        event = await receive()
        if event.get("type") == "http.disconnect":
            raise ValueError("client disconnected")
        if event.get("type") != "http.request":
            continue
        chunk = event.get("body", b"")
        if not isinstance(chunk, bytes):
            raise ValueError("request body must be bytes")
        remaining = max_bytes + 1 - len(chunks)
        if remaining > 0:
            chunks.extend(chunk[:remaining])
        if len(chunks) > max_bytes or len(chunk) > remaining:
            raise RequestBodyTooLarge("request body too large")
        if not event.get("more_body", False):
            break
    value = json.loads(chunks.decode("utf-8") or "{}")
    if not isinstance(value, dict):
        raise ValueError("request body must be an object")
    return value
