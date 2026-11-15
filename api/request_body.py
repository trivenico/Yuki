"""Shared bounded JSON request-body reader for ASGI APIs."""
from __future__ import annotations

import json


class RequestBodyTooLarge(ValueError):
    """Raised when an ASGI request body exceeds its endpoint budget."""


async def read_json_object(receive, max_bytes: int) -> dict:
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
            raise ValueError("request body chunk must be bytes")
        if len(chunks) + len(chunk) > max_bytes:
            raise RequestBodyTooLarge("request body too large")
        chunks.extend(chunk)
        if not event.get("more_body", False):
            break
    try:
        value = json.loads(chunks.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid JSON") from None
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value
