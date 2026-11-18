"""Fail-closed consistency checks for versioned prompt/template bundles."""
from __future__ import annotations
from string import Formatter
import hashlib
import json
import re
from typing import Any

class TemplateConsistencyError(ValueError):
    """A template bundle is incomplete, inconsistent, or unsafe to render."""

def _template_fields(template: str) -> set[str]:
    fields = set()
    try:
        for _, field, format_spec, conversion in Formatter().parse(template):
            if field is None:
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field):
                raise TemplateConsistencyError("template placeholders must be simple named fields")
            if format_spec or conversion:
                raise TemplateConsistencyError("format specifiers and conversions are not supported")
            fields.add(field)
    except ValueError as exc:
        raise TemplateConsistencyError("template contains malformed braces") from exc
    return fields

def check_template_bundle(manifest: dict[str, Any], templates: dict[str, str]) -> dict[str, Any]:
    """Validate exact template names, semantic versions and placeholder declarations."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise TemplateConsistencyError("unsupported or missing template manifest schema_version")
    version = manifest.get("bundle_version")
    if not isinstance(version, str) or not re.fullmatch(r"\\d+\\.\\d+\\.\\d+", version):
        raise TemplateConsistencyError("bundle_version must use semantic version format")
    specs = manifest.get("templates")
    if not isinstance(specs, dict) or not specs or not isinstance(templates, dict) or set(specs) != set(templates):
        raise TemplateConsistencyError("manifest and template names must match exactly")
    normalized = {}
    for name, spec in specs.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name):
            raise TemplateConsistencyError("template names must be stable identifiers")
        if not isinstance(spec, dict) or spec.get("version") != version:
            raise TemplateConsistencyError(f"template {name} version does not match bundle version")
        required = spec.get("required_variables")
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise TemplateConsistencyError(f"template {name} requires a variable list")
        if len(required) != len(set(required)):
            raise TemplateConsistencyError(f"template {name} contains duplicate required variables")
        content = templates.get(name)
        if not isinstance(content, str) or not content.strip() or len(content) > 32_000:
            raise TemplateConsistencyError(f"template {name} must be non-empty and at most 32,000 characters")
        if _template_fields(content) != set(required):
            raise TemplateConsistencyError(f"template {name} placeholders do not match its manifest")
        normalized[name] = {"version": version, "content": content, "required_variables": sorted(required)}
    canonical = json.dumps({"schema_version": 1, "bundle_version": version, "templates": normalized},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"schema_version": 1, "bundle_version": version, "template_count": len(normalized),
            "bundle_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "status": "consistent"}
