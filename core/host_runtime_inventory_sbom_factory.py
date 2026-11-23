"""Environment-backed factory for the managed host agent's trusted SBOM collector.

All paths and keys come from the root/service-managed systemd environment.
No task request can select a manifest path, key, or publisher identity.
"""
from __future__ import annotations

import os

from core.host_runtime_inventory_ipc import load_private_hmac_key
from core.trusted_sbom_inventory import (
    DigestBoundSBOMVerifier,
    ReadOnlySBOMManifestStore,
    build_sbom_inventory_collector,
)


def build_collector():
    """Return a digest-bound SBOM collector for host_runtime_inventory_service_cli."""
    try:
        service_uid_text = os.environ["YUKI_HOST_INVENTORY_SERVICE_UID"]
        if not service_uid_text.isascii() or not service_uid_text.isdigit():
            raise ValueError
        service_uid = int(service_uid_text)
        directory = os.environ["YUKI_HOST_INVENTORY_SBOM_DIRECTORY"]
        key_path = os.environ["YUKI_HOST_INVENTORY_SBOM_PUBLISHER_KEY_FILE"]
    except (KeyError, ValueError) as exc:
        raise RuntimeError("trusted SBOM deployment configuration is incomplete.") from exc

    publisher_key = load_private_hmac_key(key_path, expected_uid=service_uid)
    store = ReadOnlySBOMManifestStore(
        directory=directory,
        expected_uid=service_uid,
    )
    verifier = DigestBoundSBOMVerifier(publisher_key=publisher_key)
    return build_sbom_inventory_collector(manifest_lookup=store, verifier=verifier)
