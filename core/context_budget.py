"""Context-window accounting from explicitly configured limits and measured usage.

This module deliberately does not guess model context limits. Providers and callers
must supply a known positive limit and a non-negative measured token count.
"""
from __future__ import annotations

from typing import Any


def context_budget(*, limit_tokens: int | None, used_tokens: int | None,
                   estimated: bool = False) -> dict[str, Any]:
    """Return a safe context budget; invalid or missing metadata stays unknown."""
    unknown = {
        "limit_tokens": None,
        "used_tokens": None,
        "remaining_tokens": None,
        "estimated": bool(estimated),
        "available": False,
        "scope": "latest_request",
        "detail": "Model context limit or usage is unknown; configure explicit metadata.",
    }
    if not isinstance(estimated, bool):
        raise ValueError("estimated must be a boolean")
    if (type(limit_tokens) is not int or limit_tokens <= 0
            or type(used_tokens) is not int or used_tokens < 0
            or used_tokens > limit_tokens):
        return unknown
    return {
        "limit_tokens": limit_tokens,
        "used_tokens": used_tokens,
        "remaining_tokens": limit_tokens - used_tokens,
        "estimated": estimated,
        "available": True,
        "scope": "latest_request",
        "detail": (
            "Context usage is estimated from token counts."
            if estimated else
            "Context usage uses provider-reported token counts and an explicitly configured limit."
        ),
    }
