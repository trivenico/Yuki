"""Shared, versioned canonical request identity for specialist-agent plans."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

AGENT_PLAN_DIGEST_VERSION = 1


def canonical_agent_plan_request(
    *, tenant_id: str, owner_principal_id: str, subtasks: Iterable[Mapping],
) -> str:
    """Return the canonical JSON envelope used by every plan persistence backend.

    Callers validate domain-specific limits before invoking this helper. Both
    coordinator and repository pass the same normalized field names so changes
    to routing, permissions, dependencies, approval, or action alter the digest.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("agent_plan_digest_tenant_invalid")
    if not isinstance(owner_principal_id, str) or not owner_principal_id.strip():
        raise ValueError("agent_plan_digest_owner_invalid")
    normalized = []
    seen = set()
    for item in subtasks:
        if not isinstance(item, Mapping):
            raise ValueError("agent_plan_digest_subtask_invalid")
        subtask_id = item.get("subtask_id")
        if not isinstance(subtask_id, str) or not subtask_id:
            raise ValueError("agent_plan_digest_subtask_id_invalid")
        if subtask_id in seen:
            raise ValueError("agent_plan_digest_duplicate_subtask")
        seen.add(subtask_id)
        capabilities = item.get("required_capabilities", item.get("capabilities", []))
        permissions = item.get("required_permissions", item.get("permissions", []))
        dependencies = item.get("depends_on", item.get("dependencies", []))
        action = item.get("action", "emit")
        approval = item.get("requires_approval", True)
        description = item.get("description")
        agent_id = item.get("agent_id")
        for value, error in (
            (description, "description"), (agent_id, "agent_id"), (action, "action"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError("agent_plan_digest_" + error + "_invalid")
        for values, error in (
            (capabilities, "capabilities"), (permissions, "permissions"),
            (dependencies, "dependencies"),
        ):
            if (not isinstance(values, (list, tuple))
                    or any(not isinstance(value, str) or not value for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError("agent_plan_digest_" + error + "_invalid")
        if type(approval) is not bool:
            raise ValueError("agent_plan_digest_approval_invalid")
        normalized.append({
            "subtask_id": subtask_id,
            "agent_id": agent_id,
            "action": action,
            "description": description.strip(),
            "required_capabilities": sorted(capabilities),
            "required_permissions": sorted(permissions),
            "depends_on": sorted(dependencies),
            "requires_approval": approval,
        })
    if not normalized:
        raise ValueError("agent_plan_digest_subtasks_empty")
    envelope = {
        "version": AGENT_PLAN_DIGEST_VERSION,
        "tenant_id": tenant_id,
        "owner_principal_id": owner_principal_id,
        "subtasks": sorted(normalized, key=lambda item: item["subtask_id"]),
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def agent_plan_request_digest(*, tenant_id: str, owner_principal_id: str,
                              subtasks: Iterable[Mapping]) -> str:
    canonical = canonical_agent_plan_request(
        tenant_id=tenant_id, owner_principal_id=owner_principal_id, subtasks=subtasks,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
