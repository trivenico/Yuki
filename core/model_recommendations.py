"""Conservative, explainable model-size guidance from detected host resources."""
from __future__ import annotations

import math

_GIB = 1024 ** 3


def recommend_model_profiles(hardware, *, task="general", max_results=4):
    """Suggest parameter-size bands, not specific checkpoints or guaranteed fit."""
    if not isinstance(hardware, dict) or hardware.get("schema_version") != 1:
        raise ValueError("unsupported hardware report")
    if task not in {"general", "coding", "reasoning", "multilingual"}:
        raise ValueError("unsupported task")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 10:
        raise ValueError("max_results must be between 1 and 10")

    accelerators = hardware.get("accelerators")
    if not isinstance(accelerators, list):
        accelerators = []
    gpu_bytes = [
        item.get("memory_total_bytes") for item in accelerators
        if isinstance(item, dict)
        and isinstance(item.get("memory_total_bytes"), int)
        and not isinstance(item.get("memory_total_bytes"), bool)
        and item["memory_total_bytes"] > 0
    ]
    gpu_gib = max(gpu_bytes) / _GIB if gpu_bytes else None
    ram_bytes = hardware.get("ram_total_bytes")
    ram_gib = ram_bytes / _GIB if isinstance(ram_bytes, int) and not isinstance(ram_bytes, bool) and ram_bytes > 0 else None
    disk_bytes = hardware.get("disk_free_bytes")
    disk_gib = disk_bytes / _GIB if isinstance(disk_bytes, int) and not isinstance(disk_bytes, bool) and disk_bytes >= 0 else None

    if gpu_gib is not None:
        tiers = [
            (4, "1B–3B", "small", "4-bit quantization recommended"),
            (8, "7B–8B", "balanced", "4-bit quantization; short context"),
            (12, "8B–14B", "balanced", "4-bit quantization; monitor KV-cache memory"),
            (16, "14B", "large", "4-bit quantization; context length materially affects fit"),
            (24, "30B–32B", "large", "4-bit quantization; validate offload and KV cache"),
            (48, "65B–70B", "xlarge", "multi-GPU or aggressive quantization may be required"),
        ]
        eligible = [tier for tier in tiers if gpu_gib >= tier[0]]
        source = "gpu_vram"
        capacity = gpu_gib
    elif ram_gib is not None:
        tiers = [
            (8, "1B–3B", "small", "CPU inference; quantization recommended"),
            (16, "3B–7B", "balanced", "CPU inference; quantization strongly recommended"),
            (32, "7B–14B", "large", "CPU inference; expect slower generation"),
            (64, "14B–32B", "xlarge", "CPU inference; check memory headroom and context"),
        ]
        eligible = [tier for tier in tiers if ram_gib >= tier[0]]
        source = "system_ram"
        capacity = ram_gib
    else:
        eligible = []
        source = "unknown"
        capacity = None

    if not eligible:
        eligible = [(0, "cloud or remote runtime", "fallback", "Local hardware capacity could not be established; verify manually")]
    else:
        eligible = eligible[-2:]

    task_notes = {
        "general": "Compare instruction-following quality and multilingual support",
        "coding": "Prefer code-specialized checkpoints and run a coding benchmark",
        "reasoning": "Evaluate reasoning quality; parameter count alone is not a quality measure",
        "multilingual": "Check target-language coverage and tokenizer behavior",
    }
    results = []
    for threshold, size_band, tier_name, caution in eligible:
        results.append({
            "parameter_band": size_band,
            "profile": tier_name,
            "resource_basis": source,
            "detected_capacity_gib": round(capacity, 1) if capacity is not None else None,
            "task": task,
            "task_guidance": task_notes[task],
            "caution": caution,
            "requires_license_review": True,
            "requires_model_card_review": True,
            "automatic_download": False,
            "automatic_activation": False,
        })
    if disk_gib is not None and disk_gib < 10:
        results.insert(0, {
            "parameter_band": "defer local download",
            "profile": "storage_limited",
            "resource_basis": "disk_free",
            "detected_capacity_gib": round(disk_gib, 1),
            "task": task,
            "task_guidance": "Free storage or select a smaller checkpoint before downloading",
            "caution": "Model weights, temporary download files and caches require additional space",
            "requires_license_review": True,
            "requires_model_card_review": True,
            "automatic_download": False,
            "automatic_activation": False,
        })
    return results[:max_results]
