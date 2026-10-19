"""Reviewed built-in manifest skills with bounded subprocess execution.

Only read-only workspace listing is registered here. This is process separation,
not a complete OS sandbox; the worker independently enforces workspace containment.
"""
from __future__ import annotations

from pathlib import Path
import sys

from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest


def workspace_list_manifest() -> SkillManifest:
    return SkillManifest.from_dict({
        "skill_id": "workspace.list",
        "version": "1.0.0",
        "entrypoint": "core.skills.workspace_list_worker",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 512},
                "limit": {"type": "integer", "enum": [10, 25, 50, 100]},
            },
            "required": ["path", "limit"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 512},
                "entries": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "maxLength": 255},
                            "type": {"type": "string", "enum": ["file", "directory", "symlink", "other"]},
                        },
                        "required": ["name", "type"],
                        "additionalProperties": False,
                    },
                },
                "truncated": {"type": "boolean"},
            },
            "required": ["path", "entries", "truncated"],
            "additionalProperties": False,
        },
        "capabilities": ["workspace.read"],
        "risk": "low",
        "timeout_seconds": 5,
        "max_output_bytes": 16384,
        "parallel_safe": True,
        "resource_group": "workspace.read",
    })


def register_builtin_workspace_list(dispatcher: SkillDispatcher, workspace: str | Path) -> None:
    """Register the single reviewed read-only skill using a fixed worker executable."""
    root = Path(workspace).expanduser().resolve()
    worker = Path(__file__).resolve().parent / "skills" / "workspace_list_worker.py"
    dispatcher.register_subprocess(
        workspace_list_manifest(),
        [sys.executable, str(worker), str(root)],
        cwd=str(root),
    )


def workspace_read_text_manifest() -> SkillManifest:
    return SkillManifest.from_dict({
        "skill_id": "workspace.read_text",
        "version": "1.0.0",
        "entrypoint": "core.skills.workspace_read_text_worker",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 512},
                "max_bytes": {"type": "integer", "enum": [1024, 4096, 8192, 16384]},
            },
            "required": ["path", "max_bytes"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 512},
                "text": {"type": "string", "maxLength": 16384},
                "truncated": {"type": "boolean"},
            },
            "required": ["path", "text", "truncated"],
            "additionalProperties": False,
        },
        "capabilities": ["workspace.read"],
        "risk": "low",
        "timeout_seconds": 5,
        "max_output_bytes": 20000,
        "parallel_safe": True,
        "resource_group": "workspace.read",
    })


def register_builtin_workspace_read_text(dispatcher: SkillDispatcher, workspace: str | Path) -> None:
    """Register bounded UTF-8 text reading for explicitly non-sensitive workspace files."""
    root = Path(workspace).expanduser().resolve()
    worker = Path(__file__).resolve().parent / "skills" / "workspace_read_text_worker.py"
    dispatcher.register_subprocess(
        workspace_read_text_manifest(),
        [sys.executable, str(worker), str(root)],
        cwd=str(root),
    )
