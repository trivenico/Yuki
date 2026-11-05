"""Evidence-only readiness assessment for installed local model candidates.

This module combines inventory, catalog provenance, resource estimates and
benchmark records. It produces recommendations only; it never selects a
provider, downloads weights, loads a model, or changes runtime state.
"""
from __future__ import annotations

import math

from providers.ollama_catalog_reconciliation import reconcile_ollama_inventory
from providers.ollama_model_inventory import assess_resource_fit


def assess_local_model_readiness(
    installed_models, catalog, evaluation_registry, hardware_snapshot, *,
    benchmark_id, max_results=20,
):
    if not isinstance(evaluation_registry, dict) or not isinstance(
        evaluation_registry.get("records"), list
    ):
        raise ValueError("invalid_evaluation_registry")
    if not isinstance(hardware_snapshot, dict):
        raise ValueError("invalid_hardware_snapshot")
    if not isinstance(benchmark_id, str) or not benchmark_id or len(benchmark_id) > 128:
        raise ValueError("invalid_benchmark_id")
    if type(max_results) is not int or not 1 <= max_results <= 100:
        raise ValueError("invalid_max_results")

    reconciliation = reconcile_ollama_inventory(installed_models, catalog)
    catalog_by_key = {
        (item.get("model_id"), item.get("model_version")): item
        for item in catalog.get("models", []) if isinstance(item, dict)
    }
    eval_by_id = {}
    for record in evaluation_registry["records"]:
        if isinstance(record, dict) and record.get("benchmark_id") == benchmark_id:
            eval_by_id.setdefault(record.get("model_id"), []).append(record)

    available_host = hardware_snapshot.get("available_memory_bytes")
    available_gpu = hardware_snapshot.get("gpu", {}).get("memory_bytes")
    candidates, excluded = [], []
    for installed, match in zip(
        installed_models, reconciliation["installed_models"]
    ):
        blockers = []
        if match["status"] != "digest_match" or not match["matched_artifact"]:
            blockers.append("artifact_digest_not_verified")
            artifact = None
        else:
            key = (
                match["matched_artifact"]["model_id"],
                match["matched_artifact"]["model_version"],
            )
            artifact = catalog_by_key.get(key)
            if artifact is None:
                blockers.append("catalog_artifact_missing")
        if artifact is not None:
            if artifact.get("ownership_status") != "verified":
                blockers.append("ownership_not_verified")
            if artifact.get("state") not in {"evaluated", "approved", "staged", "active"}:
                blockers.append("artifact_not_evaluated")
            if artifact.get("evaluation_passed") is not True:
                blockers.append("catalog_evaluation_not_passed")
        records = eval_by_id.get(
            artifact.get("model_id") if artifact else installed.name, []
        )
        evaluation = records[-1] if records else None
        if evaluation is None:
            blockers.append("benchmark_evidence_missing")
        else:
            if evaluation.get("artifact_sha256") != match.get("installed_digest"):
                blockers.append("evaluation_artifact_digest_mismatch")
            if evaluation.get("license_status") != "verified":
                blockers.append("license_not_verified")
            if evaluation.get("runtime_status") != "verified":
                blockers.append("runtime_not_verified")
            coverage = evaluation.get("coverage")
            if isinstance(coverage, bool) or not isinstance(coverage, (int, float)) or not math.isfinite(coverage):
                blockers.append("valid_coverage_missing")
            elif not 0 <= coverage <= 1:
                blockers.append("coverage_out_of_range")
            if type(evaluation.get("successful_cases")) is not int or evaluation["successful_cases"] < 1:
                blockers.append("successful_benchmark_cases_missing")
        resource = None
        if type(available_host) is int and available_host >= 0:
            resource = assess_resource_fit(
                installed, available_host_memory_bytes=available_host,
                available_gpu_memory_bytes=available_gpu,
            )
            if resource["status"] != "plausible_fit":
                blockers.append("resource_fit_not_established")
        else:
            blockers.append("host_memory_snapshot_missing")

        candidate = {
            "installed_name": installed.name,
            "artifact": match["matched_artifact"],
            "benchmark_id": benchmark_id,
            "coverage": evaluation.get("coverage") if evaluation else None,
            "mean_latency_ms": evaluation.get("mean_latency_ms") if evaluation else None,
            "resource_assessment": resource,
            "blockers": blockers,
            "recommendation_status": "eligible_for_human_review" if not blockers else "not_eligible",
            "activation_status": "manual_approval_required",
            "automatic_activation": False,
        }
        if blockers:
            excluded.append(candidate)
        else:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (
        -float(item["coverage"]),
        float(item["mean_latency_ms"]) if isinstance(item["mean_latency_ms"], (int, float)) and math.isfinite(item["mean_latency_ms"]) else float("inf"),
        item["installed_name"],
    ))
    for rank, item in enumerate(candidates[:max_results], 1):
        item["rank"] = rank
    return {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "recommendations": candidates[:max_results],
        "excluded_candidates": excluded,
        "catalog_mutated": False,
        "runtime_mutated": False,
        "automatic_download": False,
        "automatic_activation": False,
        "limitations": [
            "Memory estimates are low-confidence heuristics and require live runtime measurement.",
            "Benchmark coverage is task-specific and does not establish general capability or safety.",
            "Eligibility means only that supplied evidence meets this report's checks.",
            "A separate human approval and runtime policy decision is required before activation.",
        ],
    }
