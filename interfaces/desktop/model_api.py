"""Stable desktop-facing facade over Yuki's shared model services.

Native desktop shells should call these functions rather than reimplement
model discovery, hardware inspection or selection policy.
"""
from core.model_manager import get_hardware_report, get_model_catalog

__all__ = ["get_hardware_report", "get_model_catalog"]
