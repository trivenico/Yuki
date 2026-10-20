"""Versioned manifest adapters for audited built-in skills."""
from __future__ import annotations

import sys

from core.skill_manifest import SkillManifest


def time_skill_manifest() -> SkillManifest:
    """Return the strict v1 contract for the read-only local clock skill."""
    return SkillManifest.from_dict({
        "skill_id": "system.time.read",
        "version": "1.0.0",
        "entrypoint": "core.builtin_skill_worker.time",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "local_time": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "required": ["local_time"],
            "additionalProperties": False,
        },
        "capabilities": ["system.clock.read"],
        "risk": "low",
        "timeout_seconds": 5,
        "max_output_bytes": 8192,
    })


def time_skill_command() -> tuple[str, ...]:
    """Return a fixed interpreter/module command; never derive it from user input."""
    return (sys.executable, "-m", "core.builtin_skill_worker")
