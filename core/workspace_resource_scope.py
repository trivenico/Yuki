"""Resolve model-proposed workspace paths into canonical, bounded scopes.

This module performs path validation only. It does not open files, prevent
filesystem TOCTOU races, or grant permission to read or write anything.
Callers must use descriptor-relative, no-follow filesystem operations and
revalidate the resolved target at the point of use.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re


_SCOPE_VERSION = "yuki.workspace-scope.v1"
_INVALID_COMPONENT = re.compile(r"[\x00-\x1f\x7f]")


class WorkspaceScopeError(ValueError):
    """Raised when a requested resource escapes or violates workspace policy."""


@dataclass(frozen=True, slots=True)
class WorkspaceResource:
    workspace_id: str
    relative_path: str
    canonical_path: str
    scope_id: str


class WorkspaceResourceScopeResolver:
    """Resolve relative paths strictly inside one configured workspace root."""

    def __init__(self, workspace_root: str | os.PathLike[str], *, workspace_id: str = "default"):
        if not isinstance(workspace_id, str) or not workspace_id.strip() or len(workspace_id) > 128:
            raise ValueError("workspace_id must be non-empty bounded text")
        if not isinstance(workspace_root, (str, os.PathLike)):
            raise TypeError("workspace_root must be a path")
        raw_root = os.fspath(workspace_root)
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ValueError("workspace_root must be non-empty text")
        if _INVALID_COMPONENT.search(raw_root):
            raise ValueError("workspace_root contains control characters")
        root = Path(raw_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise WorkspaceScopeError("workspace root must be an existing directory")
        self._root = root
        self._workspace_id = workspace_id

    @property
    def root(self) -> Path:
        return self._root

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    def resolve(self, requested_path: str | os.PathLike[str], *, allow_root: bool = False) -> WorkspaceResource:
        if not isinstance(requested_path, (str, os.PathLike)):
            raise TypeError("requested_path must be text or a path")
        raw = os.fspath(requested_path)
        if not isinstance(raw, str) or not raw or len(raw) > 4096:
            raise WorkspaceScopeError("requested path must be non-empty and at most 4096 characters")
        if _INVALID_COMPONENT.search(raw):
            raise WorkspaceScopeError("requested path contains control characters")
        # Normalize separators for validation; absolute paths and drive/UNC
        # forms are intentionally not accepted as model-facing input.
        normalized = raw.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
            raise WorkspaceScopeError("absolute paths are not permitted")
        if normalized == ".":
            if not allow_root:
                raise WorkspaceScopeError("workspace root itself is not a valid resource")
            parts = ()
        else:
            parts = PurePosixPath(normalized).parts
            if not parts or any(part in ("", ".", "..") for part in parts):
                raise WorkspaceScopeError("path traversal and empty path components are not permitted")
        if any(":" in part for part in parts):
            raise WorkspaceScopeError("alternate data stream and drive syntax are not permitted")

        candidate = self._root.joinpath(*parts)
        try:
            canonical = candidate.resolve(strict=False)
            relative = canonical.relative_to(self._root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceScopeError("requested path cannot be resolved inside workspace") from exc
        if not relative.parts:
            if not allow_root:
                raise WorkspaceScopeError("workspace root itself is not a valid resource")
            relative_text = "."
        else:
            relative_text = relative.as_posix()
        # The target may not exist yet, but its nearest existing ancestor must
        # resolve within the workspace. Path.resolve(strict=False) provides
        # that lexical/symlink-aware check; descriptor-relative operations are
        # still required to close races after this method returns.
        material = "\0".join((_SCOPE_VERSION, self._workspace_id, str(self._root), relative_text))
        scope_id = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return WorkspaceResource(
            workspace_id=self._workspace_id,
            relative_path=relative_text,
            canonical_path=str(canonical),
            scope_id=scope_id,
        )
