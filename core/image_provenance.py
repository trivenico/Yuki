"""Strict binding between a pinned application image and Docker image metadata.

Docker inspect is control-plane evidence, not a cryptographic attestation.
This validator prevents identity substitution by requiring the container's
image ID, the inspected image object's ID, and the exact repository digest
reference to agree before any host runtime evidence is accepted.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from core.host_runtime_inventory import RuntimeInventoryError

_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,255}@sha256:[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_REPO_DIGESTS = 256


def verify_docker_image_identity(
    *,
    image_ref: str,
    container_image_id: str,
    image_inspect: Mapping[str, object],
) -> str:
    """Return the pinned digest only when all independently observed IDs agree."""
    if type(image_ref) is not str or not _IMAGE_REF.fullmatch(image_ref):
        raise RuntimeInventoryError("configured application image is not immutably digest-pinned.")
    if type(container_image_id) is not str or not _IMAGE_ID.fullmatch(container_image_id):
        raise RuntimeInventoryError("container image ID is malformed.")
    if not isinstance(image_inspect, Mapping):
        raise RuntimeInventoryError("Docker image inspection metadata is unavailable.")
    inspected_id = image_inspect.get("Id")
    if type(inspected_id) is not str or not _IMAGE_ID.fullmatch(inspected_id):
        raise RuntimeInventoryError("Docker image inspection returned an invalid image ID.")
    if inspected_id != container_image_id:
        raise RuntimeInventoryError("container image ID does not match the inspected Docker image object.")
    repo_digests = image_inspect.get("RepoDigests")
    if not isinstance(repo_digests, list) or len(repo_digests) > _MAX_REPO_DIGESTS:
        raise RuntimeInventoryError("Docker image repository digest metadata is unavailable or oversized.")
    if any(type(item) is not str or len(item) > 320 for item in repo_digests):
        raise RuntimeInventoryError("Docker image repository digest metadata contains invalid entries.")
    if image_ref not in repo_digests:
        raise RuntimeInventoryError("Docker image RepoDigest does not match the pinned immutable application image.")
    return image_ref.rsplit("@", 1)[1]
