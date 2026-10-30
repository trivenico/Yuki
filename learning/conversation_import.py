"""Consent-based, offline importer for common AI conversation JSON exports.

Imported conversations are evidence for review, not instructions or training data.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 20_000_000
MAX_MESSAGES = 100_000
MAX_MESSAGE_CHARS = 30_000
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
)


def redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _content_text(value: Any, depth: int = 0) -> str:
    if depth > 8:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_content_text(item, depth + 1) for item in value)))
    if isinstance(value, dict):
        parts = []
        for key in ("text", "content", "parts", "value"):
            if key in value:
                found = _content_text(value[key], depth + 1)
                if found:
                    parts.append(found)
        return "\n".join(parts)
    return ""


def _extract_messages(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        candidates = payload["messages"]
    elif isinstance(payload, dict) and isinstance(payload.get("mapping"), dict):
        candidates = []
        for node in payload["mapping"].values():
            if isinstance(node, dict) and isinstance(node.get("message"), dict):
                candidates.append(node["message"])
    elif isinstance(payload, dict) and isinstance(payload.get("conversations"), list):
        candidates = []
        for conversation in payload["conversations"]:
            if isinstance(conversation, dict):
                candidates.extend(_extract_messages(conversation))
    else:
        raise ValueError("Unsupported export: expected messages, conversations, a ChatGPT mapping, or a message list.")

    output = []
    for item in candidates[:MAX_MESSAGES]:
        if not isinstance(item, dict):
            continue
        author = item.get("author")
        role = item.get("role")
        if not isinstance(role, str) and isinstance(author, dict):
            role = author.get("role")
        if not isinstance(role, str):
            role = "unknown"
        content = item.get("content", item.get("text", ""))
        text = redact_secrets(_content_text(content).strip())[:MAX_MESSAGE_CHARS]
        if not text:
            continue
        output.append({
            "role": role[:40].lower(),
            "text": text,
            "source_id": str(item.get("id", ""))[:128],
        })
    return output


class ConversationImportStore:
    """Stores normalized conversation records locally after explicit consent."""

    def __init__(self, path: str | Path = "data/imported_conversations.json"):
        self.path = Path(path).expanduser().resolve()

    def preview(self, source: str | Path) -> dict:
        path = Path(source).expanduser().resolve(strict=True)
        if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".json":
            raise ValueError("Only a regular JSON export file is supported.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Conversation export exceeds the 20 MB limit.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Export is not valid UTF-8 JSON.") from exc
        messages = _extract_messages(payload)
        return {
            "source_file": path.name,
            "message_count": len(messages),
            "roles": sorted({message["role"] for message in messages}),
            "redacted_message_count": sum("[REDACTED]" in m["text"] for m in messages),
            "messages": messages,
        }

    def import_file(self, source: str | Path, *, user_consented: bool = False) -> dict:
        if user_consented is not True:
            raise PermissionError("Explicit user consent is required before importing conversation history.")
        preview = self.preview(source)
        messages = preview["messages"]
        if not messages:
            raise ValueError("No supported text messages were found.")
        raw_hash = hashlib.sha256(
            json.dumps(messages, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        record = {
            "id": raw_hash,
            "source_file": preview["source_file"],
            "message_count": len(messages),
            "messages": messages,
            "training_status": "not_eligible_pending_review",
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Import store is unreadable; no data was overwritten.") from exc
            if not isinstance(existing, list):
                raise RuntimeError("Import store has an invalid structure.")
        if any(isinstance(item, dict) and item.get("id") == raw_hash for item in existing):
            return {"imported": False, "duplicate": True, "id": raw_hash, "message_count": len(messages)}
        existing.append(record)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)
        return {"imported": True, "duplicate": False, "id": raw_hash, "message_count": len(messages),
                "training_status": record["training_status"]}

    def delete(self, conversation_id: str) -> bool:
        if not isinstance(conversation_id, str) or not conversation_id:
            return False
        if not self.path.exists():
            return False
        records = json.loads(self.path.read_text(encoding="utf-8"))
        remaining = [item for item in records if not isinstance(item, dict) or item.get("id") != conversation_id]
        if len(remaining) == len(records):
            return False
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
        return True
