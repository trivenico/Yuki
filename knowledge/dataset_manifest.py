"""Deterministic dataset manifests and split-leakage validation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Mapping

from knowledge.annotation import AnnotationDataset

SCHEMA_VERSION = 1
SPLITS = frozenset({"train", "validation", "test"})


class DatasetManifestError(ValueError):
    """Raised when a dataset manifest or split assignment is invalid."""


@dataclass(frozen=True, slots=True)
class AssetSplit:
    asset_id: str
    split: str
    leakage_group: str


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    schema_version: int
    dataset_id: str
    source_dataset_sha256: str
    manifest_sha256: str
    asset_splits: tuple[AssetSplit, ...]
    split_counts: tuple[tuple[str, int], ...]
    provenance: str = "owner_supplied_unverified"
    verified: bool = False


def _identifier(value: object, field: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DatasetManifestError(f"invalid {field}")
    return value


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_dataset_manifest(
    dataset: AnnotationDataset,
    *,
    dataset_id: str,
    split_assignments: Mapping[str, str],
    leakage_groups: Mapping[str, str] | None = None,
) -> DatasetManifest:
    """Create a stable manifest; assets in one leakage group must share a split."""
    dataset_id = _identifier(dataset_id, "dataset_id", 128)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", dataset_id):
        raise DatasetManifestError("invalid dataset_id")
    if not isinstance(dataset, AnnotationDataset):
        raise DatasetManifestError("dataset must be an AnnotationDataset")
    if not dataset.annotations:
        raise DatasetManifestError("cannot create a manifest for an empty dataset")
    if not isinstance(split_assignments, Mapping):
        raise DatasetManifestError("split_assignments must be a mapping")

    asset_ids = {item.asset_id for item in dataset.annotations}
    if set(split_assignments) != asset_ids:
        raise DatasetManifestError("split assignments must cover exactly the dataset assets")
    groups = leakage_groups if leakage_groups is not None else {}
    if not isinstance(groups, Mapping) or set(groups) - asset_ids:
        raise DatasetManifestError("leakage groups may only reference dataset assets")

    entries = []
    group_splits: dict[str, str] = {}
    for asset_id in sorted(asset_ids):
        split = split_assignments[asset_id]
        if not isinstance(split, str) or split not in SPLITS:
            raise DatasetManifestError(f"unsupported split for asset {asset_id}")
        group = _identifier(groups.get(asset_id, asset_id), "leakage_group", 256)
        previous = group_splits.setdefault(group, split)
        if previous != split:
            raise DatasetManifestError("assets in the same leakage group cannot cross dataset splits")
        entries.append(AssetSplit(asset_id=asset_id, split=split, leakage_group=group))

    annotation_payload = [asdict(item) for item in sorted(dataset.annotations, key=lambda item: item.annotation_id)]
    evidence_payload = [asdict(item) for item in sorted(dataset.evidence, key=lambda item: item.evidence_id)]
    source_digest = _canonical_digest({"annotations": annotation_payload, "evidence": evidence_payload})
    counts = tuple((name, sum(item.split == name for item in entries)) for name in ("train", "validation", "test"))
    manifest_payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "source_dataset_sha256": source_digest,
        "asset_splits": [asdict(item) for item in entries],
        "split_counts": counts,
        "provenance": "owner_supplied_unverified",
        "verified": False,
    }
    return DatasetManifest(
        schema_version=SCHEMA_VERSION,
        dataset_id=dataset_id,
        source_dataset_sha256=source_digest,
        manifest_sha256=_canonical_digest(manifest_payload),
        asset_splits=tuple(entries),
        split_counts=counts,
    )
