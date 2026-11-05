"""Adaptive local model admission using CPU-first device budgets.

This combines the host execution profile with conservative installed-model
memory estimates. It is advisory only: no model is loaded, downloaded,
activated, or sent to a remote provider.
"""
from __future__ import annotations

from providers.device_execution_profile import build_execution_profile
from providers.ollama_model_inventory import InstalledModel, assess_resource_fit


def assess_installed_models_for_device(
    installed_models,
    hardware_snapshot,
    *,
    gpu_available_memory_bytes=None,
    max_results=100,
):
    if not isinstance(installed_models, (list, tuple)) or len(installed_models) > 500:
        raise ValueError("invalid_installed_models")
    if any(not isinstance(model, InstalledModel) for model in installed_models):
        raise ValueError("invalid_installed_model_record")
    if type(max_results) is not int or not 1 <= max_results <= 500:
        raise ValueError("invalid_max_results")

    profile = build_execution_profile(
        hardware_snapshot,
        gpu_available_memory_bytes=gpu_available_memory_bytes,
    )
    eligible, excluded = [], []
    for model in installed_models:
        assessment = assess_resource_fit(
            model,
            available_host_memory_bytes=profile["model_runtime_memory_budget_bytes"],
            available_gpu_memory_bytes=gpu_available_memory_bytes,
        )
        item = {
            "model_name": model.name,
            "model_digest": model.digest,
            "disk_size_bytes": model.size_bytes,
            "quantization": model.quantization,
            "resource_assessment": assessment,
            "execution_mode": profile["execution_mode"],
            "automatic_load": False,
            "automatic_activation": False,
        }
        if profile["status"] != "ready_for_compact_cpu_inference":
            item["blocker"] = "device_memory_budget_too_low"
            excluded.append(item)
        elif assessment["status"] != "plausible_fit":
            item["blocker"] = "model_exceeds_device_runtime_budget"
            excluded.append(item)
        else:
            item["recommendation_status"] = "eligible_for_runtime_benchmark"
            eligible.append(item)

    eligible.sort(key=lambda item: (
        item["disk_size_bytes"], item["model_name"]
    ))
    excluded.sort(key=lambda item: (
        item["disk_size_bytes"], item["model_name"]
    ))
    return {
        "schema_version": 1,
        "device_profile": profile,
        "eligible_models": eligible[:max_results],
        "excluded_models": excluded[:max_results],
        "automatic_download": False,
        "automatic_load": False,
        "automatic_activation": False,
        "limitations": [
            "Resource estimates are conservative heuristics, not a runtime guarantee.",
            "Eligible models still require a measured smoke test and peak-memory benchmark.",
            "This report does not establish model license, provenance, safety or quality.",
            "Model readiness and release approval remain separate mandatory gates.",
        ],
    }
