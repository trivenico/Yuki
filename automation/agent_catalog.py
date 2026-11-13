"""Machine-readable, non-executable catalogue of Yuki specialist roles.

The catalogue describes target responsibilities only. It never registers or
executes handlers and cannot grant tools or permissions.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "yuki_agent_catalog.json"
_AGENT_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_STATES = frozenset({
    "proposed", "specified", "implemented", "evaluated", "approved",
    "enabled", "suspended", "retired",
})
_RISKS = frozenset({"low", "medium", "high", "critical"})
_REQUIRED = frozenset({
    "agent_id", "name", "division", "responsibility", "lifecycle_state",
    "risk", "required_permissions", "allowed_tool_classes",
    "handler_ref", "evaluation_ref",
})


def validate_agent_catalog(value: Any) -> list[str]:
    """Return structural and lifecycle errors without importing agent handlers."""
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["catalog must be a JSON object"]
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if value.get("catalog_id") != "yuki-specialist-workforce":
        errors.append("catalog_id is invalid")
    if value.get("catalog_status") != "target_catalog":
        errors.append("catalog_status must remain target_catalog")
    if value.get("owner_authorization_required") is not True:
        errors.append("owner authorization must be required")
    agents = value.get("agents")
    if not isinstance(agents, list) or len(agents) != 100:
        errors.append("agents must contain exactly 100 proposed roles")
        return errors

    seen: set[str] = set()
    for index, agent in enumerate(agents):
        prefix = f"agents[{index}]"
        if not isinstance(agent, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = _REQUIRED - set(agent)
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
            continue
        agent_id = agent["agent_id"]
        if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
            errors.append(f"{prefix}.agent_id is invalid")
        elif agent_id in seen:
            errors.append(f"{prefix}.agent_id is duplicated")
        else:
            seen.add(agent_id)
        for field in ("name", "division", "responsibility"):
            item = agent[field]
            if not isinstance(item, str) or not item.strip() or len(item) > 2000:
                errors.append(f"{prefix}.{field} must be bounded non-empty text")
        if agent["lifecycle_state"] not in _STATES:
            errors.append(f"{prefix}.lifecycle_state is invalid")
        if agent["risk"] not in _RISKS:
            errors.append(f"{prefix}.risk is invalid")
        for field in ("required_permissions", "allowed_tool_classes"):
            items = agent[field]
            if (not isinstance(items, list)
                    or any(not isinstance(item, str) or not item.strip() for item in items)
                    or len(items) != len(set(items))):
                errors.append(f"{prefix}.{field} must be a unique list of non-empty strings")
        for field in ("handler_ref", "evaluation_ref"):
            item = agent[field]
            if item is not None and (
                not isinstance(item, str) or not item.strip() or len(item) > 300
            ):
                errors.append(f"{prefix}.{field} must be null or bounded non-empty text")
        if agent["lifecycle_state"] == "proposed":
            if agent["required_permissions"] or agent["allowed_tool_classes"]:
                errors.append(f"{prefix}: proposed roles cannot receive permissions or tools")
            if agent["handler_ref"] is not None or agent["evaluation_ref"] is not None:
                errors.append(f"{prefix}: proposed roles cannot claim handlers or evaluations")
        if agent["lifecycle_state"] == "enabled":
            if not agent["handler_ref"] or not agent["evaluation_ref"]:
                errors.append(f"{prefix}: enabled roles require handler and evaluation evidence")
            if not agent["required_permissions"] or not agent["allowed_tool_classes"]:
                errors.append(f"{prefix}: enabled roles require explicit permissions and tool classes")
    return errors


def load_agent_catalog(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the role catalogue; never imports or activates handlers."""
    source = Path(path) if path is not None else _CATALOG_PATH
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("agent catalogue cannot be loaded") from exc
    errors = validate_agent_catalog(value)
    if errors:
        raise ValueError("invalid agent catalogue: " + "; ".join(errors))
    return value
