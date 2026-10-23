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
        entries.append({
            "name": name,
            "description": str(item.get("description", ""))[:500],
            "risk": str(item.get("risk", "unknown"))[:40],
            "requires_approval": bool(item.get("requires_approval", True)),
            "input_schema": schema,
        })
    return sorted(entries, key=lambda entry: entry["name"])

def capability_inventory_json(registry):
    return json.dumps(capability_inventory(registry), ensure_ascii=False, indent=2)



def capability_readiness(registry=None, *, repository_root="."):
    """Return manifest contracts plus optional registry counts, without executing skills."""
    from core.capability_manifest import manifest_as_dicts, validate_manifest
    entries = manifest_as_dicts()
    for entry in entries:
        entry["evidence_valid"] = not any(
            error.startswith(entry["capability_id"] + ":")
            for error in validate_manifest(repository_root=repository_root)
        )
    if registry is not None:
        entries.append({
            "capability_id": "registered-skill-count",
            "name": "Registered skills (inventory only)",
            "status": "experimental",
            "summary": "Number of registered skill descriptors; not a measure of end-to-end readiness.",
            "registered_skill_count": len(registry),
            "registered_skill_names": list(registry.names()),
            "evidence_valid": True,
        })
    return entries


def capability_readiness_json(registry=None, *, repository_root="."):
    return json.dumps(capability_readiness(registry, repository_root=repository_root),
                      ensure_ascii=False, indent=2)
