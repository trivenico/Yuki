"""Desktop API facade over Yuki shared application services."""
from core.model_manager import get_hardware_report, get_model_catalog
from core.yuki_ai import YukiAI

__all__ = ["YukiAI", "get_hardware_report", "get_model_catalog"]
