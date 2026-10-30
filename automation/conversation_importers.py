"""Safe parsers for explicitly supplied conversation export files.

Parsers normalize data only. They do not import it, grant consent, execute
embedded content, follow links, or load attachments.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

_MAX_BYTES = 25 * 1024 * 1024
_MAX_RECORDS = 2_000
_MAX_CONTENT = 12_000
_ALLOWED_ROLES = {"user", "assistant", "system", "tool"}


class ConversationImportError(ValueError):
    """An export is malformed, unsupported, or exceeds safe parsing limits."""


def _decode(payload: str | bytes) -> str:
    if isinstance(payload, bytes):
        if len(payload) > _MAX_BYTES:
            raise ConversationImportError("conversation export exceeds 25 MiB.")
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ConversationImportError("conversation export must be UTF-8.") from exc
    elif isinstance(payload, str):
        if len(payload.encode("utf-8")) > _MAX_BYTES:
            raise ConversationImportError("conversation export exceeds 25 MiB.")
        text = payload.lstrip("\ufeff")
    else:
        raise ConversationImportError("payload must be UTF-8 text or bytes.")
    return text


def _content(value: Any) -> str:
    if isinstance(value, str):
        result = value
    elif isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        result = "\n".join(parts)
    elif isinstance(value, dict):
        parts = value.get("parts")
        if isinstance(parts, list):
            return _content(parts)
        result = value.get("text", "")
        if not isinstance(result, str):
            result = ""
    else:
        result = ""
    result = result.strip()
    if not result or len(result) > _MAX_CONTENT or "\x00" in result:
        return ""
    return result


def _record(record_id: Any, role: Any, content: Any, created_at: Any) -> dict | None:
    if not isinstance(role, str) or role not in _ALLOWED_ROLES:
        return None
    text = _content(content)
    if not text:
        return None
    if not isinstance(record_id, str) or not record_id.strip():
        record_id = hashlib.sha256(
            (str(role) + "\0" + str(created_at) + "\0" + text).encode("utf-8")
        ).hexdigest()
    if len(record_id) > 200:
        raise ConversationImportError("source record identifier exceeds 200 characters.")
    if isinstance(created_at, (int, float)) and not isinstance(created_at, bool):
        try:
            created_at = datetime.fromtimestamp(created_at, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            created_at = ""
    if not isinstance(created_at, str):
        created_at = ""
    return {
        "id": record_id,
        "role": role,
        "content": text,
        "created_at": created_at[:64],
    }


def parse_chatgpt_export(payload: str | bytes) -> list[dict]:
    """Parse the conversations.json shape from a ChatGPT data export."""
    try:
        root = json.loads(_decode(payload))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ConversationImportError("ChatGPT export is not valid JSON.") from exc
    if not isinstance(root, list):
        raise ConversationImportError("ChatGPT export must be a conversations array.")
    output = []
    for conversation in root:
        if not isinstance(conversation, dict):
            continue
        mapping = conversation.get("mapping")
        if not isinstance(mapping, dict):
            continue
        for node in mapping.values():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            author = message.get("author")
            role = author.get("role") if isinstance(author, dict) else None
            content = message.get("content")
            record = _record(
                message.get("id"), role, content,
                message.get("create_time", ""),
            )
            if record is not None:
                output.append(record)
                if len(output) > _MAX_RECORDS:
                    raise ConversationImportError("export contains more than 2000 supported messages.")
    return output


def parse_normalized_jsonl(payload: str | bytes) -> list[dict]:
    """Parse one normalized JSON object per line (id, role, content, created_at)."""
    text = _decode(payload)
    output = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConversationImportError(f"invalid JSONL record on line {line_number}.") from exc
        if not isinstance(item, dict):
            raise ConversationImportError(f"JSONL record on line {line_number} must be an object.")
        record = _record(
            item.get("id"), item.get("role"), item.get("content"),
            item.get("created_at", ""),
        )
        if record is not None:
            output.append(record)
            if len(output) > _MAX_RECORDS:
                raise ConversationImportError("JSONL contains more than 2000 supported messages.")
    return output
