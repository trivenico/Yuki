"""Offline Hugging Face sentence-transformer adapter for semantic memory."""
from __future__ import annotations
import math
from pathlib import Path
from threading import RLock
from typing import Sequence


class HuggingFaceLocalEmbedder:
    """Load an existing local model directory; never download or use cloud."""
    is_local = True

    def __init__(self, model_path: str, *, device: str = "cpu",
                 batch_size: int = 32, max_text_chars: int = 8000):
        if not isinstance(model_path, str) or not model_path.strip():
            raise ValueError("local_model_path_required")
        try:
            resolved = Path(model_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("local_model_directory_not_found") from exc
        if not resolved.is_dir():
            raise ValueError("local_model_path_must_be_directory")
        if device not in {"cpu", "cuda", "mps"}:
            raise ValueError("device_must_be_cpu_cuda_or_mps")
        if type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError("batch_size_must_be_1_to_256")
        if type(max_text_chars) is not int or not 1 <= max_text_chars <= 32000:
            raise ValueError("max_text_chars_must_be_1_to_32000")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("optional_dependency_missing: install requirements-embeddings.txt") from exc
        self._model = SentenceTransformer(
            str(resolved), device=device, trust_remote_code=False,
            model_kwargs={"local_files_only": True},
            tokenizer_kwargs={"local_files_only": True},
        )
        self.model_path, self.device = str(resolved), device
        self.batch_size, self.max_text_chars = batch_size, max_text_chars
        self._lock = RLock()

    def encode(self, text: str) -> Sequence[float]:
        return self.encode_many([text])[0]

    def encode_many(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence) or not 1 <= len(texts) <= 128:
            raise ValueError("batch_must_contain_1_to_128_texts")
        normalized, total = [], 0
        for text in texts:
            if not isinstance(text, str) or not text.strip() or len(text) > self.max_text_chars:
                raise ValueError("embedding_text_invalid_or_too_long")
            total += len(text)
            if total > 64000:
                raise ValueError("embedding_batch_text_limit_exceeded")
            normalized.append(text.strip())
        with self._lock:
            raw = self._model.encode(
                normalized, batch_size=self.batch_size, convert_to_numpy=True,
                normalize_embeddings=False, show_progress_bar=False,
            )
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        if not isinstance(raw, (list, tuple)) or len(raw) != len(normalized):
            raise ValueError("embedding_model_returned_invalid_batch")
        vectors, dimension = [], None
        for vector in raw:
            if hasattr(vector, "tolist"):
                vector = vector.tolist()
            if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 16384:
                raise ValueError("embedding_model_returned_invalid_vector")
            values = []
            for value in vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError("embedding_model_returned_non_finite_or_non_numeric_vector")
                values.append(float(value))
            if dimension is None:
                dimension = len(values)
            elif len(values) != dimension:
                raise ValueError("embedding_model_returned_inconsistent_dimensions")
            if not any(values):
                raise ValueError("embedding_model_returned_zero_vector")
            vectors.append(values)
        return vectors
