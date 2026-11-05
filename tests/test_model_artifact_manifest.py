import hashlib
from pathlib import Path

import pytest

from providers.model_artifact_manifest import (
    ModelArtifactManifestError,
    canonical_manifest_json,
    sha256_file,
    validate_manifest,
    verify_artifact,
)


def manifest(digest):
    return {
        "schema_version": 1,
        "model_id": "yuki/core",
        "model_version": "0.1.0",
        "artifact_sha256": digest,
        "source": "yuki_training_pipeline",
        "license_id": "Apache-2.0",
        "base_model": "opensource/base-model",
        "training_method": "lora",
        "dataset_sha256": ["a" * 64],
        "created_at": "2026-11-05T09:00:00Z",
    }


def test_hashes_artifact_and_verifies_manifest(tmp_path):
    artifact = tmp_path / "weights.bin"
    artifact.write_bytes(b"yuki-checkpoint")
    digest = hashlib.sha256(b"yuki-checkpoint").hexdigest()
    item = manifest(digest)
    assert sha256_file(artifact) == digest
    assert verify_artifact(artifact, item) is True
    assert validate_manifest(item)["provenance_review"] == "unverified"


def test_rejects_digest_mismatch(tmp_path):
    artifact = tmp_path / "weights.bin"
    artifact.write_bytes(b"actual")
    assert verify_artifact(artifact, manifest("0" * 64)) is False


@pytest.mark.parametrize("change", [
    {"artifact_sha256": "not-a-digest"},
    {"license_id": "unknown license with spaces"},
    {"training_method": "automatic_activation"},
    {"dataset_sha256": ["bad"]},
    {"created_at": "yesterday"},
    {"model_id": "../escape"},
])
def test_rejects_invalid_manifest_fields(tmp_path, change):
    item = manifest("a" * 64)
    item.update(change)
    with pytest.raises(ModelArtifactManifestError):
        validate_manifest(item)


def test_rejects_duplicate_dataset_digests():
    item = manifest("a" * 64)
    item["dataset_sha256"] = ["b" * 64, "B" * 64]
    with pytest.raises(ModelArtifactManifestError, match="duplicate_dataset_digest"):
        validate_manifest(item)


def test_rejects_symlink_artifact(tmp_path):
    target = tmp_path / "target.bin"
    target.write_bytes(b"weights")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ModelArtifactManifestError, match="symlink"):
        sha256_file(link)


def test_manifest_serialization_is_stable():
    item = manifest("C" * 64)
    assert canonical_manifest_json(item) == canonical_manifest_json(dict(reversed(list(item.items()))))
    assert '"provenance_review":"unverified"' in canonical_manifest_json(item)
