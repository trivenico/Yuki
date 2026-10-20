"""Capability inventory export for CLI and future UI surfaces."""

from __future__ import annotations
import json


def capability_inventory(registry):
    """Return JSON-safe descriptions without executing any skill."""
    entries = []
    for item in registry.describe():
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        schema = item.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "string", "description": "Skill argument passed as text."}
        entries.append(
            {
                "name": name,
                "description": str(item.get("description", ""))[:500],
                "risk": str(item.get("risk", "unknown"))[:40],
                "requires_approval": bool(item.get("requires_approval", True)),
                "input_schema": schema,
            }
        )
    return sorted(entries, key=lambda entry: entry["name"])


def capability_inventory_json(registry):
    return json.dumps(capability_inventory(registry), ensure_ascii=False, indent=2)
