"""Safe, read-only project workspace tools for Yuki specialist agents.

This module intentionally offers no shell, write, delete, or network tools.
Every path is resolved beneath an explicitly configured workspace root.
"""
from __future__ import annotations

from pathlib import Path
import os

from automation.agent_tool_gateway import AgentTool

_IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh"}
_SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


class ReadOnlyWorkspace:
    """Provide bounded listing and UTF-8 file reads confined to one workspace."""

    def __init__(self, root: str | os.PathLike[str], *, max_read_bytes: int = 256 * 1024) -> None:
        if type(max_read_bytes) is not int or not 1 <= max_read_bytes <= 1024 * 1024:
            raise ValueError("max_read_bytes must be between 1 and 1048576.")
        candidate = Path(root).expanduser()
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError("workspace root must be an existing directory.")
        self.root = candidate.resolve(strict=True)
        self.max_read_bytes = max_read_bytes

    def _resolve(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip() or "\x00" in relative_path:
            raise ValueError("workspace path must be non-empty relative text.")
        supplied = Path(relative_path)
        if supplied.is_absolute():
            raise PermissionError("absolute paths are not allowed.")
        if any(part == ".." for part in supplied.parts):
            raise PermissionError("parent-directory traversal is not allowed.")
        candidate = (self.root / supplied).resolve(strict=True)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("path resolves outside the approved workspace.") from exc
        if self._is_sensitive(candidate):
            raise PermissionError("sensitive workspace paths are not available to agents.")
        return candidate

    @staticmethod
    def _is_sensitive(path: Path) -> bool:
        parts = path.relative_to(path.anchor).parts
        if any(part in _IGNORED_DIRS or part.startswith(".") for part in parts):
            return True
        name = path.name.lower()
        return name in _SENSITIVE_NAMES or path.suffix.lower() in _SENSITIVE_SUFFIXES

    def list_files(self, path: str = ".") -> dict:
        directory = self._resolve(path)
        if not directory.is_dir():
            raise ValueError("workspace listing target must be a directory.")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            try:
                resolved = child.resolve(strict=True)
                resolved.relative_to(self.root)
            except (OSError, ValueError):
                continue
            if self._is_sensitive(resolved):
                continue
            if resolved.is_file() or resolved.is_dir():
                entries.append({
                    "name": child.name,
                    "type": "directory" if resolved.is_dir() else "file",
                })
            if len(entries) >= 100:
                break
        return {"path": path, "entries": entries}

    def read_file(self, path: str, max_bytes: int | None = None) -> dict:
        target = self._resolve(path)
        if not target.is_file():
            raise ValueError("workspace read target must be a regular file.")
        limit = self.max_read_bytes if max_bytes is None else max_bytes
        if type(limit) is not int or not 1 <= limit <= self.max_read_bytes:
            raise ValueError("max_bytes exceeds the configured read limit.")
        with target.open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise ValueError("workspace file exceeds the configured read limit.")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("workspace file is not valid UTF-8 text.") from exc
        return {"path": path, "content": content, "bytes": len(data)}

    def agent_tools(self) -> tuple[AgentTool, AgentTool]:
        """Return read-only tools requiring the workspace:read permission."""
        return (
            AgentTool(
                "workspace.list", "workspace:read", self.list_files,
                "List non-sensitive files in the approved project workspace.",
            ),
            AgentTool(
                "workspace.read", "workspace:read", self.read_file,
                "Read a bounded UTF-8 text file in the approved project workspace.",
            ),
        )
