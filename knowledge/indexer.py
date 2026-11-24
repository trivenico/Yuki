"""Local, opt-in text knowledge index with bounded, descriptor-safe workspace reads."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from core.vector_memory import VectorMemory


class KnowledgeIndexer:
    SUPPORTED = {".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml"}
    MAX_FILE_BYTES = 500_000
    MAX_FILES = 200

    def __init__(self, memory: VectorMemory):
        self.memory = memory

    @classmethod
    def _read_workspace_file(cls, path: str | Path, workspace: str | Path) -> tuple[Path, str]:
        """Read a regular file beneath workspace without following path symlinks.

        POSIX descriptor-relative traversal prevents a symlink swap between
        validation and open. Platforms without the required primitives fail
        closed rather than silently weakening the workspace boundary.
        """
        root = Path(workspace).expanduser().resolve(strict=True)
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        normalized = Path(os.path.abspath(os.fspath(candidate)))
        try:
            relative = normalized.relative_to(root)
        except ValueError as exc:
            raise ValueError("Only files inside the configured workspace can be indexed.") from exc

        parts = relative.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid workspace file path.")
        if normalized.suffix.lower() not in cls.SUPPORTED:
            raise ValueError("Unsupported knowledge file type.")
        required = ("O_NOFOLLOW", "O_DIRECTORY")
        if os.name != "posix" or any(not hasattr(os, flag) for flag in required) or os.open not in os.supports_dir_fd:
            raise ValueError("Secure workspace file access is unavailable on this platform.")

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        directory_fd = os.open(root, directory_flags)
        opened_fds = [directory_fd]
        try:
            for component in parts[:-1]:
                directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                opened_fds.append(directory_fd)
            file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
            opened_fds.append(file_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Knowledge source must be a regular file.")
            if info.st_size > cls.MAX_FILE_BYTES:
                raise ValueError("File exceeds the 500 KB indexing limit.")
            chunks = []
            remaining = cls.MAX_FILE_BYTES + 1
            while remaining:
                data = os.read(file_fd, min(65536, remaining))
                if not data:
                    break
                chunks.append(data)
                remaining -= len(data)
            raw = b"".join(chunks)
            if len(raw) > cls.MAX_FILE_BYTES:
                raise ValueError("File exceeds the 500 KB indexing limit.")
            try:
                content = raw.decode("utf-8", errors="replace").strip()
            except UnicodeError as exc:
                raise ValueError("Knowledge source is not valid text.") from exc
            return normalized, content
        finally:
            for descriptor in reversed(opened_fds):
                os.close(descriptor)

    def index_file(self, path: str | Path, *, workspace: str | Path) -> dict:
        resolved, content = self._read_workspace_file(path, workspace)
        if not content:
            raise ValueError("File is empty.")
        chunks = [content[i:i + 3000] for i in range(0, min(len(content), 30000), 3000)]
        ids = [self.memory.remember(f"Source: {resolved.name}\n{chunk}", category="lesson")["id"] for chunk in chunks]
        return {"source": resolved.name, "chunks_indexed": len(ids), "memory_ids": ids}
