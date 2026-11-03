from .base import BaseProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider
from .hybrid import HybridProvider
from .gemini_cli import GeminiCLIProvider
from .factory import create_provider
from .model_manager import LocalModelManager, OllamaLocalClient, detect_hardware, recommend_models

__all__ = [
    "BaseProvider",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "HybridProvider",
    "GeminiCLIProvider",
    "create_provider",
    "LocalModelManager",
    "OllamaLocalClient",
    "detect_hardware",
    "recommend_models",
]
