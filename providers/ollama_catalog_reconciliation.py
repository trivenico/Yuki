"""Read-only reconciliation between installed Ollama models and Yuki artifacts.

Digest matches are identity evidence. Name matches alone are only hints and
never establish artifact ownership or authorize runtime activation.
"""
from __future__ import annotations

import re

from providers.model_catalog import list_models
from providers.ollama_model_inventory import InstalledModel

_SHA256 = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


def _digest(value):
    if not isinstance(value, str):
        return None
    match = _SHA256.fullmatch(value.strip())
    return match.group(1).lower() if match else None


def reconcile_ollama_inventory(installed_models, catalog):
    """Return a read-only reconciliation report; never changes catalog state."""
    if not isinstance(installed_models, (list, tuple)):
        raise TypeError("installed_models_must_be_sequence")
    records = list_models(catalog)
    by_digest = {}
    for record in records:
        digest = _digest(record.get("artifact_sha256"))
        if digest:
            by_digest.setdefault(digest, []).append(record)

    results = []
    seen_names = set()
    for model in installed_models:
        if not isinstance(model, InstalledModel):
            raise TypeError("installed_model_record_required")
        if model.name in seen_names:
            raise ValueError("duplicate_installed_model_name")
        seen_names.add(model.name)
        digest = _digest(model.digest)
        matches = by_digest.get(digest, []) if digest else []
        if len(matches) == 1:
            record = matches[0]
            status = "digest_match"
            matched = {"model_id": record["model_id"], "model_version": record["model_version"]}
        elif len(matches) > 1:
            status = "ambiguous_digest_match"
            matched = None
        else:
            status = "unmatched"
            matched = None
        results.append({
            "installed_name": model.name,
            "installed_digest": digest,
            "status": status,
            "matched_artifact": matched,
            "ownership_status": (
                matches[0].get("ownership_status", "unverified")
                if len(matches) == 1 else "unverified"
            ),
            "catalog_state": matches[0].get("state") if len(matches) == 1 else None,
            "name_is_identity_proof": False,
            "automatic_download": False,
            "automatic_activation": False,
        })

    matched_keys = {
        (item["matched_artifact"]["model_id"], item["matched_artifact"]["model_version"])
        for item in results if item["matched_artifact"] is not None
    }
    not_installed = [
        {"model_id": record["model_id"], "model_version": record["model_version"]}
        for record in records
        if (record.get("model_id"), record.get("model_version")) not in matched_keys
    ]
    return {
        "schema_version": 1,
        "mode": "read_only_digest_reconciliation",
        "installed_models": results,
        "catalog_artifacts_not_installed": not_installed,
        "catalog_mutated": False,
        "runtime_mutated": False,
        "automatic_download": False,
        "automatic_activation": False,
    }
