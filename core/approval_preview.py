"""Bounded, conservative redaction for human-readable approval previews."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|authorization|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|cookie|session|refresh[_-]?token)",
    re.IGNORECASE,
)
_MAX_DEPTH = 6
_MAX_ITEMS = 50
_MAX_STRING = 1000


def confirmation_digest(action: dict[str, Any]) -> str:
    """Return a stable SHA-256 binding to the exact stored JSON action snapshot."""
    if not isinstance(action, dict):
        raise ValueError("action must be a JSON object")
    encoded = json.dumps(
        action, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_confirmation_digest(action: dict[str, Any], supplied_digest: str) -> bool:
    """Constant-time check that explicit consent names this exact action snapshot."""
    if not isinstance(supplied_digest, str) or re.fullmatch(r"[0-9a-f]{64}", supplied_digest) is None:
        return False
    try:
        expected = confirmation_digest(action)
    except (TypeError, ValueError, UnicodeEncodeError):
        return False
    return hmac.compare_digest(expected, supplied_digest)


def redact_preview(value: Any, *, _depth: int = 0) -> Any:
    """Return a bounded JSON-like copy; redact sensitive mapping values by key."""
    if _depth >= _MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["_truncated"] = True
                break
            safe_key = str(key)[:100]
            result[safe_key] = (
                "[REDACTED]" if _SENSITIVE_KEY.search(safe_key)
                else redact_preview(item, _depth=_depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        items = [redact_preview(item, _depth=_depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append("[truncated]")
        return items
    if isinstance(value, str):
        return value[:_MAX_STRING] + ("…[truncated]" if len(value) > _MAX_STRING else "")
    if value is None or type(value) in (bool, int, float):
        return value
    return "[unsupported]"
