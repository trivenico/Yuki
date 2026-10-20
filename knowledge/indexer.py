"""Local, opt-in text knowledge index with size limits and explicit source paths."""

from __future__ import annotations

from pathlib import Path
from core.vector_memory import VectorMemory


class KnowledgeIndexer:
    SUPPORTED = {".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml"}
    MAX_FILE_BYTES = 500_000
    MAX_FILES = 200

    def __init__(self, memory: VectorMemory):
        self.memory = memory

    def index_file(self, path: str | Path, *, workspace: str | Path) -> dict:
        root = Path(workspace).expanduser().resolve()
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents or resolved.is_symlink():
            raise ValueError("Only files inside the configured workspace can be indexed.")
        if not resolved.is_file() or resolved.suffix.lower() not in self.SUPPORTED:
            raise ValueError("Unsupported knowledge file type.")
        if resolved.stat().st_size > self.MAX_FILE_BYTES:
            raise ValueError("File exceeds the 500 KB indexing limit.")
        content = resolved.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            raise ValueError("File is empty.")
        chunks = [content[i : i + 3000] for i in range(0, min(len(content), 30000), 3000)]
        ids = [
            self.memory.remember(f"Source: {resolved.name}\n{chunk}", category="lesson")["id"]
            for chunk in chunks
        ]
        return {"source": resolved.name, "chunks_indexed": len(ids), "memory_ids": ids}
