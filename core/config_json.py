"""Strict JSON parsing helpers for security-sensitive configuration.

Python's default JSON decoder accepts duplicate object keys and keeps the last
value. Configuration must reject that ambiguity at every nesting level.
"""
from __future__ import annotations

import json
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON configuration key: {key}")
        result[key] = value
    return result


def loads_strict(text: str) -> Any:
    """Parse JSON while rejecting duplicate keys at every nesting level."""
    return json.loads(text, object_pairs_hook=_unique_object)
