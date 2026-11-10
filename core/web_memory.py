"""Opt-in, local-only memory bootstrap for the authenticated browser runtime."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.agent_memory import AgentMemoryStore
from core.agent_memory_retriever import AgentMemoryRetriever
from core.auth import BuiltInAuthService, agent_memory_authorizer
from core.embedding_index import SQLiteEmbeddingIndex
from core.reasoning.context_builder import ReasoningContextBuilder
from core.unified_knowledge_api import UnifiedKnowledgeAPI
from providers.local_embeddings import HuggingFaceLocalEmbedder


def build_web_reasoning_context(
    auth_service: BuiltInAuthService,
    workspace: str | Path,
    *,
    environ: dict[str, str] | None = None,
    embedder_factory: Callable[..., object] | None = None,
) -> ReasoningContextBuilder | None:
    """Build authenticated memory retrieval only after explicit local opt-in.

    No model download, remote embedding provider, or weaker authorization
    fallback is permitted. Missing configuration/dependencies fail startup.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service_must_be_BuiltInAuthService")
    env = os.environ if environ is None else environ
    raw_enabled = env.get("YUKI_MEMORY_ENABLED", "false").strip().lower()
    if raw_enabled in {"0", "false", "no", "off"}:
        return None
    if raw_enabled not in {"1", "true", "yes", "on"}:
        raise RuntimeError("YUKI_MEMORY_ENABLED must be true or false.")

    model_path = env.get("YUKI_EMBEDDING_MODEL_PATH", "").strip()
    model_id = env.get("YUKI_EMBEDDING_MODEL_ID", "").strip()
    device = env.get("YUKI_EMBEDDING_DEVICE", "cpu").strip().lower()
    if not model_path:
        raise RuntimeError("YUKI_EMBEDDING_MODEL_PATH is required when memory is enabled.")
    if not model_id or len(model_id) > 200:
        raise RuntimeError("YUKI_EMBEDDING_MODEL_ID is required and must be at most 200 characters.")
    if device not in {"cpu", "cuda", "mps"}:
        raise RuntimeError("YUKI_EMBEDDING_DEVICE must be cpu, cuda, or mps.")

    factory = embedder_factory or HuggingFaceLocalEmbedder
    embedder = factory(model_path, device=device)
    if getattr(embedder, "is_local", False) is not True or not callable(getattr(embedder, "encode", None)):
        raise RuntimeError("Memory embeddings must use a local encoder with an encode method.")

    workspace_path = Path(workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    database_path = str(workspace_path / "agent-memory.sqlite3")
    memory_store = AgentMemoryStore(
        database_path, authorize=agent_memory_authorizer(auth_service),
    )
    embedding_index = SQLiteEmbeddingIndex(database_path, model_id=model_id)
    retriever = AgentMemoryRetriever(
        memory_store, embed=embedder.encode, embedding_index=embedding_index,
    )
    return ReasoningContextBuilder(UnifiedKnowledgeAPI(retriever))
