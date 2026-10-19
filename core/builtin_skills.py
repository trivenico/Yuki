"""Small, reviewed built-in manifest skills for safe workspace inspection.

These handlers are trusted application code, not a sandbox for third-party plugins.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest


def register_workspace_list(dispatcher: SkillDispatcher, workspace: str | Path) -> None:
    """Register a read-only directory listing confined to the configured workspace."""
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    manifest = SkillManifest.from_dict({
        "skill_id": "workspace.list",
        "version": "1.0.0",
        "entrypoint": "core.builtin_skills.workspace_list",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "maxLength": 200}},
            "required": [],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 512},
                "entries": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 255},
                    "maxItems": 200,
                },
                "truncated": {"type": "boolean"},
            },
            "required": ["path", "entries", "truncated"],
            "additionalProperties": False,
        },
        "capabilities": ["workspace.read"],
        "risk": "low",
        "timeout_seconds": 3,
        "max_output_bytes": 16384,
        "parallel_safe": True,
        "resource_group": "workspace.read",
    })

    def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        relative = arguments.get("path", ".")
        if not isinstance(relative, str) or "\x00" in relative:
            raise ValueError("Invalid relative workspace path.")
        # Reject cross-platform absolute paths and traversal before resolving.
        if relative.startswith(("/", "\\\\")) or "\\\\" in relative or ":" in relative:
            raise ValueError("Only simple relative workspace paths are allowed.")
        candidate = Path(relative)
        if candidate.is_absolute() or any(part in {"..", "~"} for part in candidate.parts):
            raise ValueError("Workspace path traversal is not allowed.")
        target = (root / candidate).resolve(strict=True)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Workspace path escapes the configured root.") from exc
        if not target.is_dir():
            raise ValueError("Workspace path must identify a directory.")
        names = sorted(
            child.name for child in target.iterdir()
            if not child.name.startswith(".")
        )
        truncated = len(names) > 200
        names = names[:200]
        return {
            "path": "." if candidate == Path(".") else candidate.as_posix(),
            "entries": names,
            "truncated": truncated,
        }

    dispatcher.register(manifest, handler)
