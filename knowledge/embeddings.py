"""Offline embedding adapter; model weights must already exist in the local cache."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol, Sequence


class EmbeddingBackend(Protocol):
    @property
    def model_id(self) -> str: ...
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Generate normalized embeddings using an already-cached local model.

    No model download is attempted. Install sentence-transformers separately and
    pre-populate the Hugging Face cache before constructing this adapter.
    """
    def __init__(self, model, *, model_id: str):
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 300:
            raise ValueError("model_id must be non-empty text of at most 300 characters.")
        if not callable(getattr(model, "encode", None)):
            raise TypeError("model must expose encode().")
        self._model = model
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @classmethod
    def from_local_cache(cls, model_id: str, *, device: str = "cpu"):
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required.")
        try:
            from huggingface_hub import snapshot_download
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Install sentence-transformers and huggingface-hub to use local embeddings."
            ) from exc
        # local_files_only prevents network access and accidental model downloads.
        local_path = snapshot_download(repo_id=model_id, local_files_only=True)
        model = SentenceTransformer(str(Path(local_path)), device=device)
        return cls(model, model_id=model_id)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not isinstance(texts, (list, tuple)) or not texts:
            raise ValueError("texts must be a non-empty sequence.")
        if len(texts) > 256:
            raise ValueError("A maximum of 256 texts may be embedded at once.")
        if any(not isinstance(text, str) or not text.strip() or len(text) > 20000 for text in texts):
            raise ValueError("Each text must be non-empty and at most 20000 characters.")
        vectors = self._model.encode(
            list(texts), convert_to_numpy=True, normalize_embeddings=True,
            show_progress_bar=False,
        )
        result = []
        for vector in vectors:
            values = [float(value) for value in vector]
            if not values or any(not math.isfinite(value) for value in values):
                raise ValueError("Embedding model returned an invalid vector.")
            norm = math.sqrt(sum(value * value for value in values))
            if norm <= 1e-12:
                raise ValueError("Embedding model returned a zero vector.")
            result.append([value / norm for value in values])
        if len(result) != len(texts) or len({len(v) for v in result}) != 1:
            raise ValueError("Embedding model returned inconsistent dimensions.")
        return result
