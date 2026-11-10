"""Authenticated browser API adapter for owner-controlled local memory."""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs

_MEMORY_ID = re.compile(r"^[a-f0-9]{32}$")
_AGENT_ID = "yuki"
_MAX_QUERY_CHARS = 1000


async def dispatch_memory_request(store: Any, owner_id: str, method: str,
                                  path: str, receive, scope) -> tuple[int, dict] | None:
    """Handle memory routes. Owner identity is supplied only by auth middleware."""
    if path != "/v1/memory" and not path.startswith("/v1/memory/"):
        return None
    if store is None:
        return 503, {"error": "memory_unavailable"}

    if path == "/v1/memory" and method == "GET":
        try:
            raw = scope.get("query_string", b"").decode("ascii")
            query = parse_qs(raw, keep_blank_values=True, max_num_fields=2, errors="strict")
            if set(query) - {"q", "limit"} or any(len(values) != 1 for values in query.values()):
                return 400, {"error": "invalid_request"}
            limit_value = query.get("limit", ["50"])[0]
            if not re.fullmatch(r"[0-9]{1,4}", limit_value):
                return 400, {"error": "invalid_request"}
            limit = int(limit_value)
            if not 1 <= limit <= 100:
                return 400, {"error": "invalid_request"}
            if "q" in query:
                search_query = query["q"][0]
                if not search_query.strip() or len(search_query) > _MAX_QUERY_CHARS:
                    return 400, {"error": "invalid_request"}
                records = await asyncio.to_thread(
                    store.search, actor_id=owner_id, agent_id=_AGENT_ID,
                    query=search_query, limit=min(limit, 20), include_shared=False,
                )
            else:
                records = await asyncio.to_thread(
                    store.list_memories, actor_id=owner_id, agent_id=_AGENT_ID,
                    limit=limit, include_shared=False,
                )
            return 200, {"memories": records}
        except (UnicodeDecodeError, ValueError):
            return 400, {"error": "invalid_request"}
        except PermissionError:
            return 403, {"error": "memory_permission_denied"}

    if path == "/v1/memory" and method == "POST":
        try:
            payload = await _body(receive)
            if set(payload) - {"content", "category", "retention_days"}:
                return 400, {"error": "invalid_request"}
            if not isinstance(payload.get("content"), str):
                return 400, {"error": "invalid_request"}
            category = payload.get("category", "semantic")
            retention = payload.get("retention_days", 365)
            record = await asyncio.to_thread(
                store.remember, actor_id=owner_id, agent_id=_AGENT_ID,
                content=payload["content"], source="owner_explicit_save",
                category=category, confidence=1.0, verified=False,
                scope="private", retention_days=retention,
            )
            return 201, {"memory": record, "review_required": True}
        except PermissionError:
            return 403, {"error": "memory_permission_denied"}
        except ValueError:
            return 400, {"error": "invalid_request"}

    match = re.fullmatch(
        r"/v1/memory/([a-f0-9]{32})(?:/(review|retire|renew|correct))?", path
    )
    if not match or not _MEMORY_ID.fullmatch(match.group(1)):
        return 404, {"error": "not_found"}
    memory_id, action = match.groups()
    if action is None and method == "DELETE":
        try:
            deleted = await asyncio.to_thread(
                store.delete, actor_id=owner_id, agent_id=_AGENT_ID, memory_id=memory_id
            )
            return (200, {"deleted": True}) if deleted else (404, {"error": "not_found"})
        except PermissionError:
            return 403, {"error": "memory_permission_denied"}
        except ValueError:
            return 400, {"error": "invalid_request"}
    if method != "POST" or action is None:
        return 405, {"error": "method_not_allowed"}
    try:
        payload = await _body(receive)
        if action == "review":
            if set(payload) != {"status", "reason"}:
                return 400, {"error": "invalid_request"}
            record = await asyncio.to_thread(
                store.transition_lifecycle, actor_id=owner_id, agent_id=_AGENT_ID,
                memory_id=memory_id, status=payload["status"], reason=payload["reason"],
            )
        elif action == "retire":
            if set(payload) != {"reason"}:
                return 400, {"error": "invalid_request"}
            record = await asyncio.to_thread(
                store.transition_lifecycle, actor_id=owner_id, agent_id=_AGENT_ID,
                memory_id=memory_id, status="retired", reason=payload["reason"],
            )
        elif action == "renew":
            if set(payload) != {"retention_days", "reason"}:
                return 400, {"error": "invalid_request"}
            record = await asyncio.to_thread(
                store.renew, actor_id=owner_id, agent_id=_AGENT_ID,
                memory_id=memory_id, retention_days=payload["retention_days"],
                reason=payload["reason"],
            )
        else:
            if set(payload) != {"content", "source", "reason"}:
                return 400, {"error": "invalid_request"}
            record = await asyncio.to_thread(
                store.correct, actor_id=owner_id, agent_id=_AGENT_ID,
                memory_id=memory_id, content=payload["content"], source=payload["source"],
                reason=payload["reason"],
            )
        return 200, {"memory": record}
    except PermissionError:
        return 403, {"error": "memory_permission_denied"}
    except ValueError:
        return 404, {"error": "not_found_or_invalid_transition"}


async def _body(receive):
    import json
    chunks = bytearray()
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            raise ValueError("client_disconnected")
        if event["type"] != "http.request":
            continue
        chunks.extend(event.get("body", b""))
        if len(chunks) > 24_000:
            raise ValueError("request_too_large")
        if not event.get("more_body", False):
            break
    try:
        value = json.loads(chunks.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid_json") from None
    if not isinstance(value, dict):
        raise ValueError("invalid_request")
    return value
