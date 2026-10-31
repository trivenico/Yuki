"""Desktop API facade over Yuki's shared application services."""
from companion.memory import MemoryStore
from core.multi_brain import MultiBrainCoordinator
from core.model_manager import get_hardware_report, get_model_catalog
from core.vector_memory import VectorMemory
from core.yuki_ai import YukiAI
from learning.conversation_import import ConversationImportStore

__all__ = [
    "YukiAI",
    "MultiBrainCoordinator",
    "VectorMemory",
    "MemoryStore",
    "ConversationImportStore",
    "get_hardware_report",
    "get_model_catalog",
]
