"""Shared, read-only model inventory API for CLI, TUI and future desktop clients."""
from __future__ import annotations

from copy import deepcopy

from core.hardware import detect_hardware
from core.model_recommendations import recommend_model_profiles
from models.open_source_catalog import INFERENCE_RUNTIMES, MODEL_FAMILIES


def get_model_catalog(*, task="general", hardware=None):
    """Return curated discovery candidates and runtime metadata.

    Catalog membership never implies a checkpoint is license-approved,
    downloaded, compatible, or activated.
    """
    if task not in {"general", "coding", "reasoning", "multilingual", "research", "tool_use", "multimodal", "on_device"}:
        raise ValueError("unsupported task")
    families = []
    for family in MODEL_FAMILIES:
        if task != "general" and task not in family["tasks"]:
            continue
        families.append(deepcopy(family))
    report = hardware if hardware is not None else detect_hardware()
    return {
        "schema_version": 1,
        "task": task,
        "families": families,
        "runtimes": deepcopy(list(INFERENCE_RUNTIMES)),
        "recommendations": recommend_model_profiles(report, task=task if task in {"general", "coding", "reasoning", "multilingual"} else "general"),
        "policy": {
            "catalog_is_license_allowlist": False,
            "automatic_download": False,
            "automatic_activation": False,
            "checkpoint_review_required": True,
        },
    }


def get_hardware_report(*, model_cache_path=None):
    """Expose the same sanitized hardware report to every user interface."""
    return detect_hardware(model_cache_path=model_cache_path)
