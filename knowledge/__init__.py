"""Yuki's local knowledge subsystem, kept separate from personal memory."""

from knowledge.engine import KnowledgeEngine, KnowledgeResult
from knowledge.embeddings import SentenceTransformerEmbedder
from knowledge.vector_store import VectorStore
from knowledge.hybrid_search import HybridKnowledgeSearch
from knowledge.indexing_coordinator import SemanticIndexCoordinator

__all__ = [
    "KnowledgeEngine", "KnowledgeResult", "SentenceTransformerEmbedder",
    "VectorStore", "HybridKnowledgeSearch", "SemanticIndexCoordinator",
]
